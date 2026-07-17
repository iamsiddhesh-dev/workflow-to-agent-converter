"""Ambiguity scoring and the clarify-mode threshold.

The translator routes open questions into ``spec.ambiguities`` instead of silently
inventing answers (see ``translate.py``). This module turns that list into a
decision: proceed with the assumptions the translator recorded, or stop and ask
the user. The score weights each ambiguity by how much its answer would change the
design — a naming question is cheap to guess wrong, a design-shaping one is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
