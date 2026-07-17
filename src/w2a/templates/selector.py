"""Pattern selection: WorkflowSpec -> one of the 5 Pattern Vault patterns.

Deterministic first — ``spec.flow.pattern`` is a required field, so every
valid spec already names one of the five patterns. The catch is that the
translator can mislabel it (e.g. calling a straight A->B->C chain "router"
because the description used the word "route"). So the deterministic path is
gated by a structural confidence check: does the task graph actually look
like the declared pattern shape? When it doesn't, an LLM fallback scores the
spec against the five pattern descriptions and picks again. Every selection
logs its confidence and source for later debugging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from w2a.llm import LLM
from w2a.spec.model import Pattern, WorkflowSpec

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.5

_PATTERN_DESCRIPTIONS: dict[Pattern, str] = {
    "sequential": "A strict A->B->C pipeline: every task has at most one predecessor and one successor, no branching, no fan-in.",
    "router": "Classify once, then branch: one or more root tasks feed two or more downstream tasks that do NOT all chain linearly from each other.",
    "report": "Gather from multiple independent sources, then aggregate: two or more root tasks (no dependencies) all feed into one final task.",
    "approval": "Draft, then a human checkpoint, then finalize: at least one task has human_checkpoint=true.",
    "watcher": "Poll on a schedule and notify: the trigger describes a recurring or periodic check, not a one-off event.",
}


@dataclass(frozen=True)
class SelectionResult:
    pattern: Pattern
    confidence: float
    source: Literal["deterministic", "llm_fallback"]
    reasoning: str = ""


def _root_and_leaf_ids(spec: WorkflowSpec) -> tuple[set[str], set[str]]:
    depended_upon = {dep for t in spec.tasks for dep in t.depends_on}
    roots = {t.id for t in spec.tasks if not t.depends_on}
    leaves = {t.id for t in spec.tasks if t.id not in depended_upon}
    return roots, leaves


def _fan_out_exists(spec: WorkflowSpec) -> bool:
    """True if some task has two or more direct dependents (a branch point)."""
    dependents: dict[str, int] = {}
    for t in spec.tasks:
        for dep in t.depends_on:
            dependents[dep] = dependents.get(dep, 0) + 1
    return any(count >= 2 for count in dependents.values())


_WATCHER_TRIGGER_HINTS = ("poll", "every", "schedule", "periodic", "recurring", "watch", "check for", "monitor")


def structural_confidence(spec: WorkflowSpec) -> float:
    """How well the task graph's shape matches the spec's declared flow.pattern, in [0, 1]."""
    pattern = spec.flow.pattern
    roots, leaves = _root_and_leaf_ids(spec)
    single_chain = len(roots) == 1 and len(leaves) == 1 and not _fan_out_exists(spec)

    if pattern == "sequential":
        return 1.0 if single_chain else 0.3
    if pattern == "router":
        return 1.0 if (roots and _fan_out_exists(spec)) else 0.2
    if pattern == "report":
        return 1.0 if len(roots) >= 2 and len(leaves) == 1 else 0.2
    if pattern == "approval":
        return 1.0 if any(t.human_checkpoint for t in spec.tasks) else 0.2
    if pattern == "watcher":
        trigger = spec.workflow.trigger.lower()
        return 1.0 if any(h in trigger for h in _WATCHER_TRIGGER_HINTS) else 0.4
    return 0.5  # unreachable given the Literal type, kept for defensiveness


class _PatternChoice(BaseModel):
    pattern: Pattern = Field(description="The best-fitting pattern for this workflow.")
    reasoning: str = Field(description="One sentence grounding the choice in the spec's own task graph.")


def _build_fallback_prompt(spec: WorkflowSpec) -> str:
    from w2a.spec.model import human_summary

    descriptions = "\n".join(f"- {name}: {desc}" for name, desc in _PATTERN_DESCRIPTIONS.items())
    return (
        "A workflow spec's declared control-flow pattern looks like it doesn't match its own "
        "task graph. Pick the pattern that actually fits, from this list:\n"
        f"{descriptions}\n\n"
        f"SPEC:\n{human_summary(spec)}\n\n"
        "Return ONLY the JSON object for your choice."
    )


def llm_fallback_select(spec: WorkflowSpec, llm: LLM | None = None) -> SelectionResult:
    """Score the spec against the five pattern descriptions via the LLM and pick one."""
    llm = llm or LLM()
    choice = llm.call(_build_fallback_prompt(spec), response_model=_PatternChoice)
    result = SelectionResult(pattern=choice.pattern, confidence=1.0, source="llm_fallback", reasoning=choice.reasoning)
    logger.info("selector: llm_fallback chose %s (%s)", result.pattern, result.reasoning)
    return result


def select_pattern(spec: WorkflowSpec, llm: LLM | None = None, threshold: float = CONFIDENCE_THRESHOLD) -> SelectionResult:
    """Select the pattern to render: deterministic unless the task graph contradicts it."""
    confidence = structural_confidence(spec)
    if confidence >= threshold:
        result = SelectionResult(
            pattern=spec.flow.pattern,
            confidence=confidence,
            source="deterministic",
            reasoning="declared flow.pattern matches the task graph shape",
        )
        logger.info("selector: deterministic %s (confidence=%.2f)", result.pattern, result.confidence)
        return result

    logger.info(
        "selector: declared pattern %s has low structural confidence (%.2f) — falling back to LLM",
        spec.flow.pattern,
        confidence,
    )
    return llm_fallback_select(spec, llm=llm)
