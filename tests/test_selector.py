"""Phase 3.4: deterministic pattern selection + the LLM fallback for low
structural confidence. The fallback is exercised with a fake LLM so no
network or key is needed.
"""

import pytest

from tests.golden_specs import GOLDEN_SPECS, ROUTER_SPEC
from w2a.spec.model import Flow
from w2a.templates.selector import (
    _PatternChoice,
    llm_fallback_select,
    select_pattern,
    structural_confidence,
)


@pytest.mark.parametrize("name", GOLDEN_SPECS.keys())
def test_deterministic_selection_matches_declared_pattern(name):
    spec = GOLDEN_SPECS[name]
    result = select_pattern(spec)
    assert result.pattern == spec.flow.pattern
    assert result.source == "deterministic"
    assert result.confidence >= 0.5


def test_structural_confidence_low_when_declared_pattern_contradicts_graph():
    # ROUTER_SPEC's graph fans out from one classifier into three branches —
    # that is not a single A->B->C chain, so mislabeling it "sequential"
    # should score low structural confidence.
    mislabeled = ROUTER_SPEC.model_copy(
        update={"flow": Flow(pattern="sequential", edges=ROUTER_SPEC.flow.edges)}
    )
    assert structural_confidence(mislabeled) < 0.5


class _FakeLLM:
    def __init__(self, choice: _PatternChoice):
        self.choice = choice
        self.last_prompt: str | None = None

    def call(self, prompt, response_model=None, **_kw):
        self.last_prompt = prompt
        return self.choice


def test_low_confidence_triggers_llm_fallback():
    mislabeled = ROUTER_SPEC.model_copy(
        update={"flow": Flow(pattern="sequential", edges=ROUTER_SPEC.flow.edges)}
    )
    fake = _FakeLLM(_PatternChoice(pattern="router", reasoning="one classifier fans out to three branches"))

    result = select_pattern(mislabeled, llm=fake)

    assert result.source == "llm_fallback"
    assert result.pattern == "router"
    assert result.confidence == 1.0
    assert fake.last_prompt is not None
    assert "router" in fake.last_prompt


def test_llm_fallback_select_logs_and_returns_choice():
    fake = _FakeLLM(_PatternChoice(pattern="watcher", reasoning="trigger describes a recurring poll"))
    result = llm_fallback_select(ROUTER_SPEC, llm=fake)
    assert result.pattern == "watcher"
    assert result.source == "llm_fallback"
    assert "recurring poll" in result.reasoning
