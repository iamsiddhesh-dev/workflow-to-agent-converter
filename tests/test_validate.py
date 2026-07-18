"""Phase 5: the four validation tiers plus the bounded repair loop.

Each seeded-defect test proves two things: the *right* tier catches the
defect (not some other tier by accident), and — except for the generic-
scaffolding case, which is a genuine LLM-quality problem rather than a bug —
the repair loop fixes it within the 3-iteration budget. The generic-
boilerplate case instead proves the harness reports an honest ``fail``
rather than pretending a bad fill counts as progress.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.golden_specs import REPORT_SPEC, ROUTER_SPEC
from w2a.generate.gapfill import GapFills
from w2a.pipeline.graph import run_pipeline
from w2a.spec.model import WorkflowSpec
from w2a.templates.render import build_context, render_files
from w2a.validate.env_tier import run_env_tier
from w2a.validate.exec_tier import run_exec_tier
from w2a.validate.repair import FilePatch, run_validation
from w2a.validate.specificity import run_specificity_tier
from w2a.validate.static_tier import run_static_tier


class EmptyGapFillLLM:
    def call(self, prompt, response_model=None, **kw):
        assert response_model is GapFills
        return GapFills()


def _generate(spec: WorkflowSpec, out_root: Path) -> Path:
    state = run_pipeline(spec=spec, llm=EmptyGapFillLLM(), out_root=str(out_root))
    assert state["errors"] == [], state["errors"]
    return state["write_result"].project_dir


class PatchLLM:
    """Returns a fixed replacement file content, regardless of the prompt."""

    def __init__(self, content: str):
        self.content = content
        self.prompts: list[str] = []

    def call(self, prompt, response_model=None, **kw):
        self.prompts.append(prompt)
        assert response_model is FilePatch
        return FilePatch(content=self.content)


class NeverImprovesLLM:
    """Simulates an LLM whose gap-fill output stays generic no matter how it's asked."""

    def call(self, prompt, response_model=None, **kw):
        if response_model is GapFills:
            return GapFills(
                backstories={a: "A capable professional who gets the job done." for a in ("eng_gatherer", "support_gatherer", "finance_gatherer", "compiler")},
                task_bodies={t: "Process the input and produce the expected output." for t in ("gather_eng", "gather_support", "gather_finance", "compile_report")},
            )
        raise AssertionError(f"unexpected response_model {response_model}")


# --- Static tier: seeded syntax error ---------------------------------------


def test_static_tier_catches_syntax_error(tmp_path):
    project = _generate(REPORT_SPEC, tmp_path)
    original = (project / "crew.py").read_text(encoding="utf-8")
    (project / "crew.py").write_text(original + "\ndef broken(:\n    pass\n", encoding="utf-8")

    report = run_static_tier(project)
    assert not report.ok
    compile_check = next(c for c in report.checks if c.name == "py_compile")
    assert not compile_check.ok
    assert "crew.py" in compile_check.issues[0]


def test_repair_loop_fixes_syntax_error(tmp_path):
    project = _generate(REPORT_SPEC, tmp_path)
    original = (project / "crew.py").read_text(encoding="utf-8")
    (project / "crew.py").write_text(original + "\ndef broken(:\n    pass\n", encoding="utf-8")

    llm = PatchLLM(original)
    spec = REPORT_SPEC.model_copy(deep=True)
    report = run_validation(project, spec, llm=llm, max_iterations=3)

    assert report.verdict == "pass_with_repairs"
    assert any(r.tier == "static" and r.applied for r in report.repairs)
    assert (project / "crew.py").read_text(encoding="utf-8") == original
    saved = json.loads((project / "validation_report.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == "pass_with_repairs"


# --- Phase 7.4: repair-loop audit — a patch that sneaks in a hallucinated
# import must be rejected, not accepted as a "fix" ---------------------------


def test_repair_loop_rejects_sneaked_import(tmp_path):
    """``_repair_file_with_llm`` (shared by the static- and exec-tier repair
    paths) runs the same AST import-diff gate as gap-fill. A patch that fixes
    the reported syntax error but also sneaks in a new import must be rejected
    outright — not applied, not silently accepted as a passing repair."""
    project = _generate(REPORT_SPEC, tmp_path)
    original = (project / "crew.py").read_text(encoding="utf-8")
    (project / "crew.py").write_text(original + "\ndef broken(:\n    pass\n", encoding="utf-8")

    sneaky_patch = "import os_command_runner_totally_not_stdlib\n\n" + original
    llm = PatchLLM(sneaky_patch)
    spec = REPORT_SPEC.model_copy(deep=True)
    report = run_validation(project, spec, llm=llm, max_iterations=3)

    assert report.verdict == "fail"
    static_repairs = [r for r in report.repairs if r.tier == "static"]
    assert static_repairs and not static_repairs[0].applied
    assert "disallowed imports" in static_repairs[0].reason
    assert "os_command_runner_totally_not_stdlib" not in (project / "crew.py").read_text(encoding="utf-8")


# --- Env tier: seeded missing dependency ------------------------------------
#
# crewai's own transitive dependency tree already carries requests (via
# instructor) and python-dotenv (a direct crewai dependency) — so stripping
# just one line from requirements.txt while crewai itself remains listed
# never actually reproduces a missing import in a fresh venv, it's still
# satisfied transitively. Wiping requirements.txt entirely reproduces the
# real defect: nothing installs, so even ``from crewai import LLM`` in
# config.py fails, exactly the class of bug this tier exists to catch.


def test_env_tier_catches_missing_dependency(tmp_path):
    project = _generate(REPORT_SPEC, tmp_path)
    (project / "requirements.txt").write_text("", encoding="utf-8")

    report = run_env_tier(project)
    assert not report.ok
    assert any("import check failed" in issue for issue in report.issues)


def test_repair_loop_fixes_missing_dependency(tmp_path):
    project = _generate(REPORT_SPEC, tmp_path)
    (project / "requirements.txt").write_text("", encoding="utf-8")

    class UnusedLLM:
        def call(self, *a, **kw):
            raise AssertionError("env-tier repair is deterministic and must not call the LLM")

    spec = REPORT_SPEC.model_copy(deep=True)
    report = run_validation(project, spec, llm=UnusedLLM(), max_iterations=3)

    assert report.verdict == "pass_with_repairs"
    env_repair = next(r for r in report.repairs if r.tier == "env")
    assert env_repair.applied
    assert "crewai" in (project / "requirements.txt").read_text(encoding="utf-8")


# --- Phase 7.1 #8: a subprocess timeout must be a clean report, not a bare traceback ---


def test_env_tier_timeout_is_a_clean_report_not_a_traceback(tmp_path, monkeypatch):
    """Simulates the exact Phase 6 finding: under load, a subprocess call inside
    the env tier can blow its own timeout. That must surface as ``EnvTierReport(ok=False)``
    with a message — never let ``subprocess.TimeoutExpired`` propagate out of the tier."""
    import subprocess

    from w2a.validate import env_tier as env_tier_module

    project = _generate(REPORT_SPEC, tmp_path)

    def _always_times_out(argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 1))

    monkeypatch.setattr(env_tier_module.subprocess, "run", _always_times_out)

    report = run_env_tier(project)  # must not raise

    assert report.ok is False
    assert not report.venv_created
    assert any("timed out" in issue for issue in report.issues)


# --- Exec tier: seeded runtime crash -----------------------------------------


def test_exec_tier_catches_runtime_crash(tmp_path):
    project = _generate(REPORT_SPEC, tmp_path)
    main_py = project / "main.py"
    original = main_py.read_text(encoding="utf-8")
    broken = original.replace(
        'def _read_input(source: str) -> str:\n    if source == "-":\n        return sys.stdin.read()\n    return Path(source).read_text(encoding="utf-8")',
        'def _read_input(source: str) -> str:\n    return Path("/definitely/does/not/exist.txt").read_text(encoding="utf-8")',
    )
    assert broken != original, "seed did not match main_linear.j2's current _read_input body"
    main_py.write_text(broken, encoding="utf-8")

    report = run_exec_tier(project)
    assert not report.ok
    assert report.exit_code != 0
    assert any("main.py exited" in issue for issue in report.issues)


def test_repair_loop_fixes_runtime_crash(tmp_path):
    project = _generate(REPORT_SPEC, tmp_path)
    main_py = project / "main.py"
    original = main_py.read_text(encoding="utf-8")
    broken = original.replace(
        'def _read_input(source: str) -> str:\n    if source == "-":\n        return sys.stdin.read()\n    return Path(source).read_text(encoding="utf-8")',
        'def _read_input(source: str) -> str:\n    return Path("/definitely/does/not/exist.txt").read_text(encoding="utf-8")',
    )
    assert broken != original
    main_py.write_text(broken, encoding="utf-8")

    llm = PatchLLM(original)
    spec = REPORT_SPEC.model_copy(deep=True)
    report = run_validation(project, spec, llm=llm, max_iterations=3)

    assert report.verdict == "pass_with_repairs"
    exec_repair = next(r for r in report.repairs if r.tier == "exec")
    assert exec_repair.applied
    assert exec_repair.target_file == "main.py"
    assert main_py.read_text(encoding="utf-8") == original


# --- Phase 7.1 #3: exec tier must still prove periodic tasks execute ---------


def _watcher_spec_with_periodic_task() -> WorkflowSpec:
    """Mirrors the real ticket_triage.json fixture: a classify->alert watch chain
    plus a weekly report task with no deps/edges — a genuinely different cadence,
    which scheduled_watcher now runs only behind --periodic, not every poll."""
    from w2a.spec.model import AgentSpec, Flow, TaskSpec, Workflow

    return WorkflowSpec(
        workflow=Workflow(
            name="Ticket Watch", description="Watch tickets; separately, summarize the week.",
            trigger="Polls for new tickets on a recurring schedule.", category="ops",
        ),
        agents=[
            AgentSpec(id="classifier", role="Classifier", goal="classify", backstory_hint="fast"),
            AgentSpec(id="alerter", role="Alerter", goal="alert", backstory_hint="vigilant"),
            AgentSpec(id="reporter", role="Reporter", goal="report", backstory_hint="thorough"),
        ],
        tasks=[
            TaskSpec(id="classify_ticket", description="Classify the ticket.", agent_id="classifier", expected_output="a label"),
            TaskSpec(
                id="trigger_alert", description="Alert on-call if urgent.", agent_id="alerter",
                depends_on=["classify_ticket"], expected_output="alert sent or not",
            ),
            TaskSpec(
                id="generate_weekly_report", description="Summarize the week's tickets.",
                agent_id="reporter", expected_output="a weekly summary",
            ),
        ],
        tools=[],
        flow=Flow(pattern="watcher", edges=[("classify_ticket", "trigger_alert")]),
    )


def test_exec_tier_reaches_periodic_tasks_via_second_pass(tmp_path):
    """A plain --once pass only exercises the watch chain (classify/alert); the
    periodic task (weekly report) must still be proven reachable — via --periodic —
    or the exec tier would regress to accepting a project where it silently never
    runs at all, the opposite failure mode from Phase 6 finding #3 (it used to run
    on *every* poll; now it must run on *at least one* explicit invocation)."""
    spec = _watcher_spec_with_periodic_task()
    project = _generate(spec, tmp_path)

    report = run_exec_tier(project)

    assert report.ok
    assert "generate_weekly_report" in report.tasks_started
    assert "classify_ticket" in report.tasks_started
    assert "trigger_alert" in report.tasks_started
    assert not report.tasks_missing


# --- Specificity tier: seeded generic scaffolding ---------------------------


def _boilerplate_ify(spec: WorkflowSpec, pattern: str, resolutions: dict, project: Path) -> None:
    context = build_context(spec, pattern, resolutions)
    for agent in context["agents"]:
        agent["backstory"] = "A capable professional who gets the job done."
    for task in context["tasks"]:
        task["description"] = "Process the input and produce the expected output."
    files = render_files(context, pattern)
    (project / "crew.py").write_text(files["crew.py"], encoding="utf-8", newline="\n")


def test_specificity_tier_catches_generic_boilerplate(tmp_path):
    from w2a.generate.registry import resolve_all

    project = _generate(REPORT_SPEC, tmp_path)
    _boilerplate_ify(REPORT_SPEC, "report", resolve_all(REPORT_SPEC), project)

    report = run_specificity_tier(project, REPORT_SPEC)
    assert not report.ok
    assert report.verdict == "generic scaffolding"
    assert report.score < 0.6
    assert len(report.missing) > 5


def test_repair_loop_reports_honest_failure_on_generic_boilerplate(tmp_path):
    from w2a.generate.registry import resolve_all

    project = _generate(REPORT_SPEC, tmp_path)
    _boilerplate_ify(REPORT_SPEC, "report", resolve_all(REPORT_SPEC), project)

    spec = REPORT_SPEC.model_copy(deep=True)
    report = run_validation(project, spec, llm=NeverImprovesLLM(), max_iterations=3)

    assert report.verdict == "fail"
    specificity_repairs = [r for r in report.repairs if r.tier == "specificity"]
    assert len(specificity_repairs) == 3
    assert all(r.applied for r in specificity_repairs)
    final_tiers = json.loads((project / "validation_report.json").read_text(encoding="utf-8"))["tiers"]
    assert any(t["tier"] == "specificity" and not t["ok"] for t in final_tiers)


# --- Healthy generated projects should validate clean -----------------------


class UnreachableLLM:
    def call(self, *a, **kw):
        raise AssertionError("a healthy project must not need any repair LLM calls")


@pytest.mark.parametrize("spec", [ROUTER_SPEC, REPORT_SPEC], ids=["router", "report"])
def test_healthy_project_validates_pass(tmp_path, spec):
    project = _generate(spec, tmp_path)
    report = run_validation(project, spec, llm=UnreachableLLM(), max_iterations=3)
    assert report.verdict == "pass"
    assert report.repairs == []


# --- Acceptance: the two Phase-4 benchmark projects validate pass in a fresh venv ---

_HAS_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")


@pytest.mark.skipif(not _HAS_KEY, reason="no LLM API key set")
@pytest.mark.parametrize("name", ["ticket_triage", "pr_summary"], ids=["ops", "dev"])
def test_benchmark_project_validates(tmp_path, name):
    from typer.testing import CliRunner

    from w2a.cli import app

    convert_result = CliRunner().invoke(
        app,
        ["convert", f"examples/workflows/{name}.md", "--out", str(tmp_path), "--interactive"],
        input="use sensible defaults\n" * 12,
    )
    assert convert_result.exit_code == 0, convert_result.output
    project = next(d for d in tmp_path.iterdir() if d.is_dir())

    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    spec = WorkflowSpec.model_validate(manifest["spec"])

    report = run_validation(project, spec, max_iterations=3)
    assert report.verdict in {"pass", "pass_with_repairs"}, str(report)

    pattern = manifest["pattern"]["selected"]
    if pattern == "report":
        assert list(project.glob("output/*.md")), "report pattern must produce its artifact"
