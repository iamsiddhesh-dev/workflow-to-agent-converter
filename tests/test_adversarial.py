"""Phase 7.2: ambiguity hardening against adversarial input.

DoD (PLAN.md / DETAILED_PLAN.md Phase 7): a one-liner, a self-contradictory
description, a non-workflow input, and a mixed-workflow paragraph must each
produce clarify-mode or a graceful structured refusal — never a confabulated
spec (populated agents/tasks with no ambiguities recorded, silently inventing
past what the text actually supports).

The real assertion needs a real translation (network + API key); this module
also carries deterministic, no-network checks on the prompt itself so the
guardrail's presence is provable without hitting the network on every run.
"""

from __future__ import annotations

import os

import pytest

from w2a.spec.ambiguity import score
from w2a.spec.lint import lint
from w2a.spec.model import WorkflowSpec
from w2a.spec.translate import build_prompt, translate

_HAS_KEY = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY"))


def _is_confabulated(spec: WorkflowSpec) -> bool:
    """A populated design with nothing flagged as open or assumed — the failure
    mode the whole clarify-mode machinery exists to prevent."""
    return bool(spec.agents) and bool(spec.tasks) and not spec.ambiguities


def _is_graceful_refusal(spec: WorkflowSpec) -> bool:
    """Empty design + a recorded ambiguity explaining why — never silently empty."""
    return not spec.agents and not spec.tasks and bool(spec.ambiguities)


def _assert_not_confabulated(spec: WorkflowSpec, report, label: str) -> None:
    """The actual DoD bar: never a confident, fully-invented design with nothing
    flagged. Three outcomes are all legitimate per the Phase 2 ambiguity design:
    clarify mode (score >= threshold), a graceful empty refusal, or proceeding
    with the open question(s)/assumption(s) honestly recorded below threshold —
    only a populated design with *zero* trail of what it didn't know is a miss.
    """
    assert not _is_confabulated(spec), (
        f"{label} produced a confident, unflagged design: {spec.model_dump_json(indent=2)}"
    )
    if not (report.clarify or _is_graceful_refusal(spec)):
        assert spec.ambiguities or spec.assumptions, (
            f"{label}: proceeded with a populated design but recorded neither "
            f"an ambiguity nor an assumption — nothing honest to show for it"
        )


# --- Deterministic: the guardrail instructions are actually in the prompt ----


def test_prompt_instructs_against_contradiction_confabulation():
    prompt = build_prompt("some description")
    assert "CONTRADICT" in prompt
    assert "ambiguities[]" in prompt


def test_prompt_instructs_against_non_workflow_confabulation():
    prompt = build_prompt("some description")
    assert "does not describe a business process" in prompt
    assert "agents=[] and tasks=[]" in prompt


# --- Network-gated: real translation of each adversarial case ---------------

pytestmark_key = pytest.mark.skipif(not _HAS_KEY, reason="no LLM API key set")


@pytestmark_key
def test_one_liner_does_not_confabulate():
    spec = translate("automate my standup")
    _assert_not_confabulated(spec, score(spec), "one-liner")


@pytestmark_key
def test_contradictory_description_does_not_confabulate():
    description = (
        "I want a report generated every single day at 9am automatically, no one "
        "should have to ask for it. Actually it should only run when someone on the "
        "team explicitly requests it, on-demand, whenever they need one."
    )
    spec = translate(description)
    _assert_not_confabulated(spec, score(spec), "contradictory trigger")


@pytestmark_key
def test_non_workflow_input_does_not_confabulate():
    spec = translate("make me rich")
    _assert_not_confabulated(spec, score(spec), "non-workflow input")


@pytestmark_key
def test_mixed_workflow_does_not_confabulate():
    """Two unrelated processes crammed into one paragraph — should not get
    silently resolved into a single confident design that discards one of them
    without a trace."""
    description = (
        "When a new employee starts we need their laptop ordered and accounts set "
        "up in Slack and email, plus a welcome doc for their manager. Separately, "
        "when a customer requests a refund over $200, finance needs to review it "
        "and approve or deny it before it's processed."
    )
    spec = translate(description)
    _assert_not_confabulated(spec, score(spec), "mixed-workflow input")


@pytestmark_key
@pytest.mark.parametrize(
    "description",
    ["automate my standup", "make me rich"],
)
def test_adversarial_specs_are_lint_clean_or_empty(description):
    """Whatever comes back — clarify-triggering or a graceful empty refusal —
    must not be structurally broken; only a *design-clean* or *empty* spec is
    an acceptable non-confabulated outcome, never a half-built broken one."""
    spec = translate(description)
    if spec.agents or spec.tasks:
        errors = [i for i in lint(spec) if i.severity == "error"]
        assert errors == [], f"{description!r}: lint errors on a populated spec: {errors}"
