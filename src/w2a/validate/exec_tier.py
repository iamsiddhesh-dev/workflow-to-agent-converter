"""Execution tier: does the generated crew actually run, not just import cleanly?

Runs ``main.py`` in-process's own interpreter (whatever has crewai installed —
static/env tiers already proved the pinned deps resolve) with ``MOCK_MODE=1``,
so this costs zero tokens and no network call. ``main_linear.j2`` logs a
structured ``[w2a] TASK_START task=<id>`` line for every task in the spec
*before* kickoff, unconditionally — so grepping for one per spec task id is a
reliable "the crew reached and attempted every declared step" signal even for
the router pattern, where CrewAI's ConditionalTask may legitimately skip a
downstream branch the mock LLM's canned output never satisfies (a real LLM
would emit branch-relevant text; the canned one is branch-agnostic by
design). Only the report pattern gets a hard artifact assertion here: its
``output_file=...`` write happens as part of CrewAI's own task-completion
mechanism, independent of tool-calling, so it is the one artifact MOCK_MODE
can produce deterministically regardless of which LLM is wired in.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_TASK_START_RE = re.compile(r"\[w2a\] TASK_START task=(\S+)")
DUMMY_INPUT = "Sample input for offline validation dry run.\n"


@dataclass
class ExecTierReport:
    ok: bool
    exit_code: int | None
    tasks_expected: list[str]
    tasks_started: list[str]
    tasks_missing: list[str]
    artifact_ok: bool
    artifact_note: str
    issues: list[str] = field(default_factory=list)
    stdout_tail: str = ""

    def __str__(self) -> str:
        lines = [f"exec tier: {'pass' if self.ok else 'fail'} (exit={self.exit_code})"]
        lines.append(f"  tasks started: {len(self.tasks_started)}/{len(self.tasks_expected)}")
        if self.tasks_missing:
            lines.append(f"  missing: {self.tasks_missing}")
        lines.append(f"  artifact: {'ok' if self.artifact_ok else 'FAIL'} — {self.artifact_note}")
        lines.extend(f"  {issue}" for issue in self.issues)
        return "\n".join(lines)


def _leaf_task_ids(tasks: list[dict]) -> list[str]:
    depended_upon = {dep for t in tasks for dep in t.get("depends_on", [])}
    return [t["id"] for t in tasks if t["id"] not in depended_upon]


def _report_artifact_check(project_dir: Path, slug: str, tasks: list[dict]) -> tuple[bool, str]:
    leaves = _leaf_task_ids(tasks)
    expected = [project_dir / "output" / f"{slug}_{task_id}.md" for task_id in leaves]
    missing = [str(p.relative_to(project_dir)) for p in expected if not p.exists()]
    if missing:
        return False, f"missing report artifact(s): {missing}"
    return True, f"report artifact(s) present: {[str(p.relative_to(project_dir)) for p in expected]}"


def _build_argv(pattern: str) -> list[str]:
    argv = ["-"]
    if pattern == "watcher":
        argv.append("--once")
    return argv


def _run_main(
    project_dir: Path,
    python_executable: str,
    argv_tail: list[str],
    timeout: float,
    stdin_payload: str,
) -> subprocess.CompletedProcess | None:
    """Run main.py once; returns None on timeout (caller decides how to report it)."""
    argv = [python_executable, "main.py", *argv_tail]
    try:
        return subprocess.run(
            argv,
            input=stdin_payload,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(project_dir),
            env={**os.environ, "MOCK_MODE": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return None


def run_exec_tier(
    project_dir: Path,
    python_executable: str | None = None,
    timeout: float = 90.0,
    stdin_payload: str = DUMMY_INPUT,
) -> ExecTierReport:
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        return ExecTierReport(
            ok=False, exit_code=None, tasks_expected=[], tasks_started=[], tasks_missing=[],
            artifact_ok=False, artifact_note="no manifest.json — cannot determine expected tasks",
            issues=["manifest.json not found"],
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = manifest["spec"]["tasks"]
    task_ids = [t["id"] for t in tasks]
    pattern = manifest["pattern"]["selected"]
    slug = manifest["workflow"]["slug"]

    python_executable = python_executable or sys.executable

    result = _run_main(project_dir, python_executable, _build_argv(pattern), timeout, stdin_payload)
    if result is None:
        return ExecTierReport(
            ok=False, exit_code=None, tasks_expected=task_ids, tasks_started=[], tasks_missing=task_ids,
            artifact_ok=False, artifact_note="run timed out before completion",
            issues=[f"main.py did not finish within {timeout}s"],
        )

    stdout = result.stdout or ""
    started = set(_TASK_START_RE.findall(stdout))
    returncodes = [result.returncode]
    stderr_tail = (result.stderr or "").strip()[-2000:]

    # scheduled_watcher deliberately splits per-poll ("watch") tasks from tasks on
    # their own cadence ("periodic" — disconnected from the poll trigger, e.g. a
    # weekly report). A plain --once pass only ever exercises the watch chain, so
    # a periodic-tasks project needs a second --periodic pass to reach the rest.
    if pattern == "watcher":
        periodic_result = _run_main(project_dir, python_executable, ["-", "--periodic"], timeout, stdin_payload)
        if periodic_result is not None:
            started |= set(_TASK_START_RE.findall(periodic_result.stdout or ""))
            returncodes.append(periodic_result.returncode)
            if periodic_result.returncode != 0:
                stderr_tail += "\n[--periodic] " + (periodic_result.stderr or "").strip()[-2000:]

    missing = [t for t in task_ids if t not in started]

    issues: list[str] = []
    if any(rc != 0 for rc in returncodes):
        issues.append(f"main.py exited {[rc for rc in returncodes if rc != 0]}")
        issues.append(stderr_tail)
    if missing:
        issues.append(f"tasks never reached execution: {missing}")

    if pattern == "report":
        artifact_ok, artifact_note = _report_artifact_check(project_dir, slug, tasks)
        if not artifact_ok:
            issues.append(artifact_note)
    else:
        artifact_ok, artifact_note = True, (
            "no artifact hard-checked for this pattern — MOCK_MODE's LLM doesn't support "
            "function-calling, so tool-produced artifacts (e.g. outbox/) aren't guaranteed "
            "even on a healthy project; only exit code and task coverage are asserted"
        )

    ok = all(rc == 0 for rc in returncodes) and not missing and artifact_ok
    return ExecTierReport(
        ok=ok,
        exit_code=result.returncode,
        tasks_expected=task_ids,
        tasks_started=sorted(started),
        tasks_missing=missing,
        artifact_ok=artifact_ok,
        artifact_note=artifact_note,
        issues=issues,
        stdout_tail=stdout[-2000:],
    )
