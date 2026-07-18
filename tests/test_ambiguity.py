"""Phase 2.4: ambiguity scoring and the clarify-mode threshold."""

import os

import pytest

from w2a.spec.ambiguity import (
    CLARIFY_THRESHOLD,
    DESIGN_CHANGING,
    NAMING,
    TOOL_CHOICE,
    drop_answered,
    resolve_ambiguities,
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


# --- Phase 7.1 #6: no near-duplicate clarify rounds after answers are supplied ---


def test_drop_answered_removes_paraphrased_duplicate():
    remaining = drop_answered(
        ["Which system is used for issue tracking (Jira, Linear, GitHub Issues)?"],
        answered_questions=["What is the specific tool used for issue tracking (Jira, Linear, GitHub Issues)?"],
    )
    assert remaining == []


def test_drop_answered_keeps_genuinely_new_question():
    remaining = drop_answered(
        ["Should a human approve before paging on-call?"],
        answered_questions=["What is the specific tool used for issue tracking?"],
    )
    assert remaining == ["Should a human approve before paging on-call?"]


def _spec_with_ambiguities(ambiguities: list[str]) -> WorkflowSpec:
    return WorkflowSpec(
        workflow=Workflow(name="T", description="d", trigger="t", category="ops"),
        agents=[], tasks=[], tools=[],
        flow=Flow(pattern="sequential", edges=[]),
        ambiguities=ambiguities,
    )


def test_resolve_ambiguities_stops_once_no_new_questions_remain():
    """A translator that keeps re-emitting near-duplicate questions after they were
    answered must not make the CLI ask them again — this is Phase 6 finding #6:
    bug_triage's real ambiguities (reused verbatim below) got asked a near-duplicate
    second round in the field trial."""
    q_tool = "What is the specific tool used for issue tracking (Jira, Linear, GitHub Issues)?"
    q_channel = (
        "What is the specific communication channel (e.g., Slack, Email, PagerDuty) "
        "used for the human checkpoint notification?"
    )
    initial_spec = _spec_with_ambiguities([q_tool, q_channel])
    # Paraphrased, still-unresolved re-emissions of the *same* two questions.
    reasked_spec = _spec_with_ambiguities(
        [
            "Which system is used for issue tracking (Jira, Linear, GitHub Issues)?",
            "Which notification channel should be used for the human checkpoint (Slack, Email, or PagerDuty)?",
        ]
    )
    assert score(reasked_spec).clarify, "test setup: the paraphrased round must still score as ambiguous"

    asked: list[str] = []

    def fake_ask(question: str) -> str:
        asked.append(question)
        return "Jira, in Slack"

    def fake_translate(description: str, extra_context: str | None = None):
        return reasked_spec

    spec, report, answered = resolve_ambiguities(
        "some description", initial_spec, score(initial_spec), ask=fake_ask, translate_fn=fake_translate
    )

    assert len(asked) == 2, f"should ask each question exactly once, not once per round: {asked}"
    assert answered == asked
    assert spec is reasked_spec
    assert report.clarify  # honestly still ambiguous — but never re-asked


def test_resolve_ambiguities_asks_a_genuinely_new_question_in_round_two():
    """Round one resolves the tool questions; round two's re-translation surfaces a
    genuinely new (different vocabulary) question, which — unlike the near-duplicate
    case above — must actually get asked."""
    initial_spec = _spec_with_ambiguities(["What tool tracks issues?", "Which platform hosts it?"])
    round_two_spec = _spec_with_ambiguities(
        ["Should a human approve before filing?", "Should escalation happen automatically?"]
    )
    final_spec = _spec_with_ambiguities([])
    responses = [round_two_spec, final_spec]

    asked: list[str] = []

    def fake_ask(question: str) -> str:
        asked.append(question)
        return "yes"

    def fake_translate(description: str, extra_context: str | None = None):
        return responses[fake_translate.calls.pop(0)]

    fake_translate.calls = [0, 1]

    spec, report, answered = resolve_ambiguities(
        "some description", initial_spec, score(initial_spec), ask=fake_ask, translate_fn=fake_translate
    )

    assert asked == [
        "What tool tracks issues?",
        "Which platform hosts it?",
        "Should a human approve before filing?",
        "Should escalation happen automatically?",
    ]
    assert spec is final_spec
    assert not report.clarify


def test_resolve_ambiguities_gives_up_honestly_after_max_rounds():
    """If every round produces genuinely new (not near-duplicate) unresolved
    questions, the loop must still terminate — no unbounded prompting."""
    batch0 = ["Should returns over $500 need manager approval?", "Should international orders route to a different warehouse?"]
    batch1 = ["Does the mobile app need push notification support?", "Should analytics dashboards refresh hourly or daily?"]
    batch2 = ["Is customer data retained for regulatory compliance reasons?", "Should refunds be processed automatically under $50?"]

    def fake_ask(question: str) -> str:
        return "answer"

    responses = [_spec_with_ambiguities(batch1), _spec_with_ambiguities(batch2)]

    def fake_translate(description: str, extra_context: str | None = None):
        return responses.pop(0)

    initial_spec = _spec_with_ambiguities(batch0)
    spec, report, answered = resolve_ambiguities(
        "some description", initial_spec, score(initial_spec), ask=fake_ask, translate_fn=fake_translate,
        max_rounds=2,
    )
    assert answered == batch0 + batch1  # 2 rounds x 2 questions each — bounded by max_rounds, not left running forever
    assert report.clarify  # honest: still ambiguous (batch2 unresolved), but the loop gave up as designed
