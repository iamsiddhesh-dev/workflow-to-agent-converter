"""Phase 2.3: translation prompt assembly, the malformed-output re-ask path, and
the acceptance check that all 6 benchmarks translate to lint-clean specs.

The re-ask path is exercised with a fault-injecting fake at the provider layer
(``LLM._call_raw``), so no network or key is needed — the retry/dump logic under
test lives entirely in ``LLM.call``.
"""

import os

import pytest

from w2a.llm import LLM, LLMResponseError
from w2a.spec.lint import is_clean, lint
from w2a.spec.model import AgentSpec, Flow, TaskSpec, ToolSpec, Workflow, WorkflowSpec
from w2a.spec.translate import (
    _ONBOARDING_SPEC,
    _PR_SUMMARY_SPEC,
    _TICKET_TRIAGE_SPEC,
    build_prompt,
    translate,
)

_VALID_JSON = _PR_SUMMARY_SPEC.model_dump_json()


class _FaultyRaw:
    """Return malformed JSON for the first ``bad_times`` calls, then valid JSON."""

    def __init__(self, bad_times: int, good_payload: str = _VALID_JSON):
        self.calls = 0
        self.bad_times = bad_times
        self.good = good_payload

    def __call__(self, prompt: str, json_mode: bool) -> str:
        self.calls += 1
        if self.calls <= self.bad_times:
            return "{ this is not valid json"
        return self.good


class _FakeLLM:
    """Stands in for LLM in translate(); records the prompt, returns a fixed spec."""

    def __init__(self, spec: WorkflowSpec):
        self.spec = spec
        self.last_prompt: str | None = None
        self.last_model = None

    def call(self, prompt, response_model=None, **_kw):
        self.last_prompt = prompt
        self.last_model = response_model
        return self.spec


def test_build_prompt_includes_rules_examples_and_input():
    prompt = build_prompt("automate our weekly report")
    assert "Do NOT invent" in prompt
    assert "ambiguities[]" in prompt
    assert "Example (ops)" in prompt
    assert "Example (dev)" in prompt
    assert "automate our weekly report" in prompt
    # The rendered examples carry real field values from the worked specs.
    assert "Onboarding Provisioner" in prompt
    assert "Diff Analyzer" in prompt


def test_build_prompt_folds_in_extra_context():
    prompt = build_prompt("desc", extra_context="Q: daily or on-demand? A: daily")
    assert "ADDITIONAL ANSWERS" in prompt
    assert "A: daily" in prompt


def test_translate_delegates_to_llm_with_response_model():
    fake = _FakeLLM(_ONBOARDING_SPEC)
    result = translate("some onboarding description", llm=fake)
    assert result is _ONBOARDING_SPEC
    assert fake.last_model is WorkflowSpec
    assert "some onboarding description" in fake.last_prompt


def test_reask_recovers_after_malformed_then_valid(monkeypatch):
    llm = LLM(max_retries=2)  # 3 attempts total
    faulty = _FaultyRaw(bad_times=2)
    monkeypatch.setattr(llm, "_call_raw", faulty)
    result = llm.call("prompt", response_model=WorkflowSpec)
    assert isinstance(result, WorkflowSpec)
    assert faulty.calls == 3  # two malformed re-asks, then success


def test_reask_exhausted_raises_and_saves_raw(monkeypatch):
    llm = LLM(max_retries=2)
    faulty = _FaultyRaw(bad_times=99)
    monkeypatch.setattr(llm, "_call_raw", faulty)
    with pytest.raises(LLMResponseError) as exc:
        llm.call("prompt", response_model=WorkflowSpec)
    assert exc.value.raw_output  # raw malformed output is preserved for debugging
    assert faulty.calls == 3


# --- Phase 7.1 #4: router workflows must fan out, not collapse into one task ---


def test_build_prompt_includes_router_fan_out_guidance():
    prompt = build_prompt("some description")
    assert "fan out" in prompt
    assert "Example (router" in prompt
    assert "classify_ticket" in prompt  # the worked example's own task id


def test_ticket_triage_worked_example_has_real_fan_out():
    """The router worked example itself must model the shape the rule prescribes:
    two tasks depending on one classifier, which does no handling of its own."""
    from w2a.templates.selector import structural_confidence

    dependents = {}
    for t in _TICKET_TRIAGE_SPEC.tasks:
        for dep in t.depends_on:
            dependents[dep] = dependents.get(dep, 0) + 1
    assert max(dependents.values()) >= 2
    assert structural_confidence(_TICKET_TRIAGE_SPEC) >= 0.5


# --- Phase 7.1 #5: a declared tool must be wired to the task that needs it ---


class _SequenceLLM:
    """Returns each spec in order, one per call — for exercising translate()'s
    bounded unused-tool retry pass without a network call."""

    def __init__(self, specs: list[WorkflowSpec]):
        self.specs = list(specs)
        self.prompts: list[str] = []

    def call(self, prompt, response_model=None, **_kw):
        self.prompts.append(prompt)
        return self.specs.pop(0)


def _bug_triage_like_spec(bug_tracker_wired: bool) -> WorkflowSpec:
    """Mirrors the real bug_triage.json fixture: a declared bug_tracker_api tool,
    wired into file_issue's description only if ``bug_tracker_wired``."""
    file_issue_description = (
        "Create an issue in the bug tracker with the severity label and suggested owner assigned."
        if bug_tracker_wired
        else "Create an issue in the tracking system with the severity label and suggested owner assigned."
    )
    return WorkflowSpec(
        workflow=Workflow(name="Bug Triage", description="d", trigger="a bug report arrives", category="ops"),
        agents=[AgentSpec(id="filer", role="Filer", goal="file bugs", backstory_hint="careful")],
        tasks=[
            TaskSpec(
                id="file_issue",
                description=file_issue_description,
                agent_id="filer",
                expected_output="issue filed",
            ),
        ],
        tools=[
            ToolSpec(
                id="bug_tracker_api",
                name="Bug Tracker Connector",
                purpose="File bug reports with appropriate metadata.",
                category="external",
                inputs="severity, owner",
                outputs="issue link",
            ),
        ],
        flow=Flow(pattern="sequential", edges=[]),
    )


def test_translate_retries_once_when_declared_tool_is_unused():
    unused = _bug_triage_like_spec(bug_tracker_wired=False)
    fixed = _bug_triage_like_spec(bug_tracker_wired=True)
    assert "unused_tool" in {i.code for i in lint(unused)}
    assert "unused_tool" not in {i.code for i in lint(fixed)}

    llm = _SequenceLLM([unused, fixed])
    result = translate("bug triage description", llm=llm)

    assert result is fixed
    assert len(llm.prompts) == 2
    assert "bug_tracker_api" in llm.prompts[1] or "Bug Tracker Connector" in llm.prompts[1]
    assert "unused_tool" not in {i.code for i in lint(result)}


def test_translate_does_not_retry_when_no_tool_is_unused():
    fixed = _bug_triage_like_spec(bug_tracker_wired=True)
    llm = _SequenceLLM([fixed])
    result = translate("bug triage description", llm=llm)
    assert result is fixed
    assert len(llm.prompts) == 1  # no wasted retry call when nothing's wrong


def test_translate_unused_tool_retry_is_bounded():
    """If the retry still doesn't fix it, translate() must not loop forever —
    it returns the last attempt (still lint-dirty) rather than calling forever."""
    unused = _bug_triage_like_spec(bug_tracker_wired=False)
    still_unused = _bug_triage_like_spec(bug_tracker_wired=False)
    llm = _SequenceLLM([unused, still_unused])
    result = translate("bug triage description", llm=llm)
    assert result is still_unused
    assert len(llm.prompts) == 2  # exactly one retry, not unbounded


# --- Acceptance: real translation of all 6 benchmarks (network + key required) ---

_BENCHMARKS = [
    "onboarding",
    "ticket_triage",
    "weekly_report",
    "review_routing",
    "pr_summary",
    "bug_triage",
]


@pytest.mark.skipif(
    not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="no LLM API key set",
)
@pytest.mark.parametrize("name", _BENCHMARKS)
def test_benchmark_translates_lint_clean(name):
    from pathlib import Path

    text = Path("examples/workflows") / f"{name}.md"
    spec = translate(text.read_text(encoding="utf-8"))
    issues = [i for i in lint(spec) if i.severity == "error"]
    assert issues == [], f"{name} lint errors: {[str(i) for i in issues]}"
    assert is_clean(spec)
