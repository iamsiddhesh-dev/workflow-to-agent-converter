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
from w2a.spec.model import WorkflowSpec
from w2a.spec.translate import (
    _ONBOARDING_SPEC,
    _PR_SUMMARY_SPEC,
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
