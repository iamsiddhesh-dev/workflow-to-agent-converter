"""The Proof Gate: run every validation tier, and when one fails, let the LLM
patch *only* the offending file and try again — bounded, and honest when it
can't fix it.

A LangGraph cycle, same idiom as the converter pipeline: ``validate`` runs the
tier battery; a conditional edge either ends the run (all tiers green, or the
iteration budget is spent) or routes to ``repair``, which dispatches on
*which* tier failed:

- ``static``  — patch the offending file with the LLM, same shape as gap-fill
  but replacing a whole file instead of a prose slot; the AST import-diff gate
  (``new_imports`` from ``gapfill.py``) runs on every candidate, so a repair
  that sneaks in a hallucinated import is rejected exactly like a bad gap-fill.
- ``env``     — a missing dependency is a ``requirements.txt`` gap, not a code
  bug: parsed deterministically from the ``ModuleNotFoundError`` and appended,
  no LLM call needed.
- ``exec``    — the traceback names the failing file; same LLM-patch-plus-gate
  path as ``static``, with the traceback as the problem statement.
- ``specificity`` — not a bug to patch but a weak gap-fill; re-run gap-fill
  with the missing domain nouns spelled out as an explicit retry hint.

``repair`` always routes back to ``validate`` unless the attempt couldn't even
be applied, in which case there is nothing left to try and the run ends early
rather than burning the rest of the budget on an identical failure.
"""

from __future__ import annotations

import ast
import json
import operator
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from w2a.generate.gapfill import gap_fill, imported_modules, new_imports
from w2a.generate.registry import resolve_all
from w2a.llm import LLM, LLMError
from w2a.spec.model import WorkflowSpec, human_summary
from w2a.validate.env_tier import run_env_tier
from w2a.validate.exec_tier import run_exec_tier
from w2a.validate.specificity import run_specificity_tier
from w2a.validate.static_tier import (
    ALLOWED_THIRD_PARTY,
    PROJECT_LOCAL,
    check_compile,
    check_import_allowlist,
    check_ruff,
    run_static_tier,
)

MAX_REPAIR_ITERATIONS = 3
VALIDATION_REPORT_NAME = "validation_report.json"

_KNOWN_PACKAGES = {
    "requests": "requests>=2.32.0",
    "dotenv": "python-dotenv>=1.0.0",
    "crewai": "crewai>=0.80.0",
}


class FilePatch(BaseModel):
    content: str = Field(description="The complete corrected file content, replacing the file verbatim.")


@dataclass
class TierOutcome:
    tier: str
    ok: bool
    detail: str


@dataclass
class RepairAttempt:
    iteration: int
    tier: str
    target_file: str | None
    applied: bool
    reason: str


@dataclass
class ValidationReport:
    verdict: str  # "pass" | "pass_with_repairs" | "fail"
    tiers: list[TierOutcome] = field(default_factory=list)
    repairs: list[RepairAttempt] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "tiers": [{"tier": t.tier, "ok": t.ok, "detail": t.detail} for t in self.tiers],
            "repairs": [
                {
                    "iteration": r.iteration,
                    "tier": r.tier,
                    "target_file": r.target_file,
                    "applied": r.applied,
                    "reason": r.reason,
                }
                for r in self.repairs
            ],
        }

    def __str__(self) -> str:
        lines = [f"verdict: {self.verdict}", "", "tiers:"]
        for t in self.tiers:
            lines.append(f"  [{'ok' if t.ok else 'FAIL'}] {t.tier}")
        if self.repairs:
            lines.append("\nrepairs:")
            for r in self.repairs:
                status = "applied" if r.applied else "not applied"
                lines.append(f"  iteration {r.iteration} [{r.tier}] {status} ({r.target_file}) — {r.reason}")
        return "\n".join(lines)


class RepairState(TypedDict, total=False):
    project_dir: Path
    spec: WorkflowSpec
    pattern: str
    resolutions: dict
    llm: LLM
    iteration: int
    max_iterations: int
    tiers: list[TierOutcome]
    last_repair_tier: str | None
    repairs: Annotated[list[RepairAttempt], operator.add]


def _run_tiers(project_dir: Path, spec: WorkflowSpec, include_env: bool, resolutions: dict | None = None) -> list[TierOutcome]:
    tiers: list[TierOutcome] = []

    static_report = run_static_tier(project_dir)
    tiers.append(TierOutcome("static", static_report.ok, str(static_report)))
    if not static_report.ok:
        return tiers

    if include_env:
        env_report = run_env_tier(project_dir)
        tiers.append(TierOutcome("env", env_report.ok, str(env_report)))
        if not env_report.ok:
            return tiers

    exec_report = run_exec_tier(project_dir)
    tiers.append(TierOutcome("exec", exec_report.ok, str(exec_report)))
    if not exec_report.ok:
        return tiers

    specificity_report = run_specificity_tier(project_dir, spec, resolutions=resolutions)
    tiers.append(TierOutcome("specificity", specificity_report.ok, str(specificity_report)))
    return tiers


def _extract_filename(issue: str) -> str | None:
    match = re.match(r"^(\S+\.py)\b", issue)
    return match.group(1) if match else None


def _failing_file_from_text(text: str) -> str | None:
    candidates = {f"{m}.py" for m in PROJECT_LOCAL}
    for path_str in reversed(re.findall(r'File "([^"]+)"', text)):
        name = Path(path_str).name
        if name in candidates:
            return name
    return None


def _missing_module_from_text(text: str) -> str | None:
    match = re.search(r"No module named '([\w.]+)'", text)
    return match.group(1).split(".")[0] if match else None


def _repair_file_with_llm(
    project_dir: Path, filename: str, problem: str, spec: WorkflowSpec, llm: LLM, iteration: int, tier: str
) -> RepairAttempt:
    path = project_dir / filename
    if not path.exists():
        return RepairAttempt(iteration, tier, filename, False, f"{filename} does not exist in the project")
    original = path.read_text(encoding="utf-8")

    prompt = (
        "You are repairing one file in an already-generated CrewAI project. "
        "Fix ONLY the reported problem; keep every other line, structure, and behavior "
        "identical wherever possible. Do not add new imports, new tools, new files, or "
        "new third-party dependencies — if the fix seems to need one, find the smallest "
        "change that avoids it instead.\n\n"
        f"Workflow spec (for grounding, not for structure):\n{human_summary(spec)}\n\n"
        f"File: {filename}\n"
        f"Problem:\n{problem}\n\n"
        f"Current file content:\n```python\n{original}\n```\n\n"
        'Return ONLY JSON: {"content": "<the complete corrected file>"}.'
    )
    try:
        patch = llm.call(prompt, response_model=FilePatch)
    except LLMError as exc:
        return RepairAttempt(iteration, tier, filename, False, f"LLM patch call failed: {exc}")

    candidate = patch.content
    try:
        ast.parse(candidate)
    except SyntaxError as exc:
        return RepairAttempt(iteration, tier, filename, False, f"patch does not parse: {exc}")

    try:
        sneaked = new_imports(original, candidate)
    except SyntaxError:
        # The original itself didn't parse (that's *why* this repair ran, for a
        # static/syntax-error failure) — there's nothing to diff against, so fall
        # back to checking the candidate's own imports against the flat allowlist.
        allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | PROJECT_LOCAL
        sneaked = {m.split(".")[0] for m in imported_modules(candidate)} - allowed
    if sneaked:
        return RepairAttempt(
            iteration, tier, filename, False, f"patch introduced disallowed imports {sorted(sneaked)}"
        )

    path.write_text(candidate, encoding="utf-8", newline="\n")
    return RepairAttempt(iteration, tier, filename, True, "patched and passed the import-allowlist gate")


def _repair_static(project_dir: Path, spec: WorkflowSpec, llm: LLM, iteration: int) -> RepairAttempt:
    for check in (check_compile(project_dir), check_import_allowlist(project_dir), check_ruff(project_dir)):
        if check.ok:
            continue
        filename = _extract_filename(check.issues[0])
        if filename is None:
            continue
        return _repair_file_with_llm(project_dir, filename, "\n".join(check.issues), spec, llm, iteration, "static")
    return RepairAttempt(iteration, "static", None, False, "could not identify a failing file from static tier issues")


def _repair_env(project_dir: Path, iteration: int) -> RepairAttempt:
    report = run_env_tier(project_dir)
    if report.ok:
        return RepairAttempt(iteration, "env", None, False, "env tier already passing, nothing to repair")

    message = "\n".join(report.issues)
    module = _missing_module_from_text(message)
    if module is None:
        return RepairAttempt(iteration, "env", "requirements.txt", False, f"could not identify a missing module: {message[:300]}")

    requirements = project_dir / "requirements.txt"
    existing = requirements.read_text(encoding="utf-8") if requirements.exists() else ""
    if module in existing:
        return RepairAttempt(iteration, "env", "requirements.txt", False, f"'{module}' already listed but still failing")

    pin = _KNOWN_PACKAGES.get(module, module)
    requirements.write_text(existing.rstrip("\n") + f"\n{pin}\n", encoding="utf-8", newline="\n")
    return RepairAttempt(iteration, "env", "requirements.txt", True, f"added missing dependency '{pin}'")


def _repair_exec(project_dir: Path, spec: WorkflowSpec, llm: LLM, iteration: int) -> RepairAttempt:
    report = run_exec_tier(project_dir)
    if report.ok:
        return RepairAttempt(iteration, "exec", None, False, "exec tier already passing, nothing to repair")

    combined = "\n".join(report.issues) + "\n" + report.stdout_tail
    filename = _failing_file_from_text(combined) or "crew.py"
    problem = "\n".join(report.issues)[-3000:]
    return _repair_file_with_llm(project_dir, filename, problem, spec, llm, iteration, "exec")


def _repair_specificity(
    project_dir: Path, spec: WorkflowSpec, pattern: str, resolutions: dict, llm: LLM, iteration: int
) -> RepairAttempt:
    report = run_specificity_tier(project_dir, spec, resolutions=resolutions)
    if report.ok:
        return RepairAttempt(iteration, "specificity", None, False, "specificity tier already passing, nothing to repair")

    hint = (
        "Your previous fill scored too generic — it dropped this workflow's own vocabulary. "
        f"You MUST work these missing concepts naturally into your prose: {', '.join(report.missing[:15])}."
    )
    files, gapfill_report = gap_fill(spec, pattern, resolutions, llm=llm, retry_hint=hint)
    if gapfill_report.error:
        return RepairAttempt(iteration, "specificity", "crew.py, tools.py", False, f"gap-fill retry failed: {gapfill_report.error}")

    written = []
    for name in ("crew.py", "tools.py"):
        if name in files:
            (project_dir / name).write_text(files[name], encoding="utf-8", newline="\n")
            written.append(name)
    return RepairAttempt(
        iteration, "specificity", ", ".join(written), True,
        f"re-ran gap-fill emphasizing missing concepts: {report.missing[:10]}",
    )


def _attempt_repair(failing: TierOutcome, state: RepairState) -> RepairAttempt:
    iteration = state["iteration"] + 1
    project_dir, spec, llm = state["project_dir"], state["spec"], state["llm"]
    if failing.tier == "static":
        return _repair_static(project_dir, spec, llm, iteration)
    if failing.tier == "env":
        return _repair_env(project_dir, iteration)
    if failing.tier == "exec":
        return _repair_exec(project_dir, spec, llm, iteration)
    if failing.tier == "specificity":
        return _repair_specificity(project_dir, spec, state["pattern"], state["resolutions"], llm, iteration)
    return RepairAttempt(iteration, failing.tier, None, False, f"no repair strategy for tier '{failing.tier}'")


def build_repair_graph():
    def validate_node(state: RepairState) -> dict:
        include_env = state["iteration"] == 0 or state.get("last_repair_tier") == "env"
        return {"tiers": _run_tiers(state["project_dir"], state["spec"], include_env, state.get("resolutions"))}

    def repair_node(state: RepairState) -> dict:
        failing = next(t for t in state["tiers"] if not t.ok)
        attempt = _attempt_repair(failing, state)
        return {"repairs": [attempt], "iteration": state["iteration"] + 1, "last_repair_tier": failing.tier}

    def route_after_validate(state: RepairState) -> str:
        if all(t.ok for t in state["tiers"]):
            return "end"
        if state["iteration"] >= state["max_iterations"]:
            return "end"
        return "repair"

    def route_after_repair(state: RepairState) -> str:
        repairs = state["repairs"]
        if repairs and not repairs[-1].applied:
            return "end"
        return "validate"

    graph = StateGraph(RepairState)
    graph.add_node("validate", validate_node)
    graph.add_node("repair", repair_node)
    graph.set_entry_point("validate")
    graph.add_conditional_edges("validate", route_after_validate, {"repair": "repair", "end": END})
    graph.add_conditional_edges("repair", route_after_repair, {"validate": "validate", "end": END})
    return graph.compile()


def _write_report(project_dir: Path, report: ValidationReport) -> None:
    (project_dir / VALIDATION_REPORT_NAME).write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def run_validation(
    project_dir: Path,
    spec: WorkflowSpec,
    llm: LLM | None = None,
    max_iterations: int = MAX_REPAIR_ITERATIONS,
) -> ValidationReport:
    """Run the tier battery, repairing (bounded) on failure, and write ``validation_report.json``."""
    llm = llm or LLM()
    manifest = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    pattern = manifest["pattern"]["selected"]
    resolutions = resolve_all(spec)

    initial: RepairState = {
        "project_dir": project_dir,
        "spec": spec,
        "pattern": pattern,
        "resolutions": resolutions,
        "llm": llm,
        "iteration": 0,
        "max_iterations": max_iterations,
        "tiers": [],
        "last_repair_tier": None,
        "repairs": [],
    }
    final = build_repair_graph().invoke(initial)
    tiers: list[TierOutcome] = final["tiers"]
    repairs: list[RepairAttempt] = final["repairs"]

    if all(t.ok for t in tiers):
        verdict = "pass_with_repairs" if repairs else "pass"
    else:
        verdict = "fail"

    report = ValidationReport(verdict=verdict, tiers=tiers, repairs=repairs)
    _write_report(project_dir, report)
    return report
