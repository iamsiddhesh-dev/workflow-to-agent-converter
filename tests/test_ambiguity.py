"""Phase 2.4: ambiguity scoring and the clarify-mode threshold."""

import os

import pytest

from w2a.spec.ambiguity import (
    CLARIFY_THRESHOLD,
    DESIGN_CHANGING,
    NAMING,
    TOOL_CHOICE,
    score,
    severity,
)
from w2a.spec.model import Flow, Workflow, WorkflowSpec
from w2a.spec.translate import translate


def _spec_with(ambiguities: list[str]) -> WorkflowSpec:
    return WorkflowSpec(
        workflow=Workflow(name="T", description="d", trigger="t", category="ops"),
        agents=[],
        tasks=[],
        tools=[],
        flow=Flow(pattern="sequential", edges=[]),
        ambiguities=ambiguities,
    )


def test_severity_buckets():
    assert severity("What should we name the reporting agent?") == NAMING
    assert severity("Which tool should send the notification — Slack or email?") == TOOL_CHOICE
    assert severity("Should a manager approve before the ticket is closed?") == DESIGN_CHANGING


def test_no_ambiguities_does_not_clarify():
    report = score(_spec_with([]))
    assert report.total == 0
    assert not report.clarify


def test_single_design_question_below_threshold_proceeds():
    report = score(_spec_with(["Should there be a human approval step?"]))
    assert report.total == DESIGN_CHANGING  # 3, below default threshold of 4
    assert not report.clarify


def test_accumulated_ambiguity_triggers_clarify():
    report = score(
        _spec_with(
            [
                "How many specialist agents should tickets route to?",  # design 3
                "Which system files the ticket — Jira or Linear?",  # tool 2
            ]
        )
    )
    assert report.total == DESIGN_CHANGING + TOOL_CHOICE
    assert report.total >= CLARIFY_THRESHOLD
    assert report.clarify
    assert len(report.questions()) == 2


def test_questions_sorted_most_shaping_first():
    from w2a.spec.ambiguity import format_questions

    report = score(_spec_with(["What do we name it?", "Should a human approve first?"]))
    rendered = format_questions(report)
    # design-changing question should be listed before the naming question
    assert rendered.index("approve") < rendered.index("name")


@pytest.mark.skipif(
    not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="no LLM API key set",
)
def test_vague_input_triggers_clarify_not_confabulation():
    spec = translate("just handle my support stuff")
    report = score(spec)
    assert spec.ambiguities, "vague input should populate ambiguities, not silently invent a spec"
    assert report.clarify, f"score {report.total} should cross the clarify threshold"
