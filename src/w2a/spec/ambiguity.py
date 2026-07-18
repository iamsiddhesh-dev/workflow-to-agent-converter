"""Ambiguity scoring and the clarify-mode threshold.

The translator routes open questions into ``spec.ambiguities`` instead of silently
inventing answers (see ``translate.py``). This module turns that list into a
decision: proceed with the assumptions the translator recorded, or stop and ask
the user. The score weights each ambiguity by how much its answer would change the
design — a naming question is cheap to guess wrong, a design-shaping one is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from w2a.spec.model import WorkflowSpec

# Severity weights per PLAN.md: design-changing = 3, tool-choice = 2, naming = 1.
DESIGN_CHANGING = 3
TOOL_CHOICE = 2
NAMING = 1

CLARIFY_THRESHOLD = 4

_NAMING_HINTS = ("name", "call it", "title", "label", "what to call", "id ", "naming")
_TOOL_HINTS = (
    "tool", "which system", "which service", "integrate", "integration", "api",
    "slack", "email", "jira", "github", "channel", "tracker", "database", "spreadsheet",
    "platform", "software",
)


def severity(question: str) -> int:
    """Weight a single ambiguity question. Unknown-shape questions default to design-changing."""
    q = question.lower()
    if any(h in q for h in _NAMING_HINTS):
        return NAMING
    if any(h in q for h in _TOOL_HINTS):
        return TOOL_CHOICE
    return DESIGN_CHANGING


@dataclass(frozen=True)
class AmbiguityReport:
    total: int
    scored: list[tuple[str, int]] = field(default_factory=list)
    threshold: int = CLARIFY_THRESHOLD

    @property
    def clarify(self) -> bool:
        """True when accumulated ambiguity crosses the threshold — ask before proceeding."""
        return self.total >= self.threshold

    def questions(self) -> list[str]:
        return [q for q, _ in self.scored]


def score(spec: WorkflowSpec, threshold: int = CLARIFY_THRESHOLD) -> AmbiguityReport:
    """Score a spec's ambiguities and decide whether clarify mode should trigger."""
    scored = [(q, severity(q)) for q in spec.ambiguities]
    total = sum(s for _, s in scored)
    return AmbiguityReport(total=total, scored=scored, threshold=threshold)


def format_questions(report: AmbiguityReport) -> str:
    """Render the open questions for a human, most design-shaping first."""
    ordered = sorted(report.scored, key=lambda qs: qs[1], reverse=True)
    return "\n".join(f"  {i}. {q}" for i, (q, _) in enumerate(ordered, 1))


_QUESTION_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "into", "from", "each", "any",
    "what", "which", "should", "would", "does", "that's", "used", "specific",
}


def _question_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _QUESTION_STOPWORDS}


def _is_near_duplicate(question: str, answered: str, threshold: float) -> bool:
    """True if two questions share enough content vocabulary to be 'the same ask again'.

    Word-overlap (Jaccard) rather than exact-match: a re-translation rarely repeats a
    question verbatim, it rephrases it ("What tool tracks issues?" vs "Which system is
    used for issue tracking?") while asking the same thing.
    """
    a, b = _question_words(question), _question_words(answered)
    if not a or not b:
        return False
    overlap = len(a & b) / len(a | b)
    return overlap >= threshold


def drop_answered(ambiguities: list[str], answered_questions: list[str], threshold: float = 0.4) -> list[str]:
    """Filter out ambiguities that are near-duplicates of questions already answered.

    A re-translation should fold the user's answers in and clear the ambiguities
    they resolved (the translation prompt already asks for this), but an LLM isn't
    perfectly obedient about it — this is the deterministic backstop so clarify
    mode never re-asks a question the user just answered, only genuinely new ones.
    """
    return [
        q for q in ambiguities
        if not any(_is_near_duplicate(q, answered, threshold) for answered in answered_questions)
    ]


MAX_CLARIFY_ROUNDS = 2


def resolve_ambiguities(
    description: str,
    spec: WorkflowSpec,
    report: AmbiguityReport,
    ask: Callable[[str], str],
    translate_fn: Callable[..., WorkflowSpec],
    max_rounds: int = MAX_CLARIFY_ROUNDS,
) -> tuple[WorkflowSpec, AmbiguityReport, list[str]]:
    """Bounded interactive clarify loop.

    Each round asks only the questions that are genuinely new (not a near-duplicate
    of one already answered — see ``drop_answered``), folds the answers into
    ``extra_context``, and re-translates. Stops when the spec is no longer
    ambiguous, when nothing new is left to ask, or after ``max_rounds`` — an
    honest "still ambiguous, proceeding anyway" beats an unbounded prompt loop.
    Returns the (possibly re-translated) spec, its latest report, and every
    question actually asked across all rounds.
    """
    answered_questions: list[str] = []
    context_parts: list[str] = []
    for _ in range(max_rounds):
        if not report.clarify:
            break
        new_questions = drop_answered(report.questions(), answered_questions)
        if not new_questions:
            break
        for q in sorted(new_questions, key=severity, reverse=True):
            context_parts.append(f"Q: {q}\nA: {ask(q)}")
            answered_questions.append(q)
        spec = translate_fn(description, extra_context="\n".join(context_parts))
        report = score(spec)
    return spec, report, answered_questions
