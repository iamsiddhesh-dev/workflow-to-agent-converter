"""Shared text-tokenization for content-word extraction.

Used by the spec linter (tool-usage overlap), the specificity tier (domain-noun
coverage), and router condition-keyword extraction — one stopword list so a
tightening in one place (Phase 7.1 #7) can't drift out of sync between callers.
"""

from __future__ import annotations

import re

# Structural/function words that carry no domain meaning in any workflow.
_STRUCTURAL_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "into", "from", "each", "any",
    "per", "over", "them", "then", "when", "what", "which", "their", "your",
    "task", "tool", "workflow", "agent", "create", "provide", "generate",
}

# Generic descriptors/verbs that show up constantly in LLM-written backstories
# and task prose regardless of domain ("analytical engineer with a strong grasp
# on...", "qualifies as small based on the changed files"). Per RESULTS.md Phase
# 6 finding #7, these drowned out true domain nouns (auth, payments) in the
# specificity tier's missing-concepts list without ever flipping a verdict — pure
# noise. Curated from the actual missing-concepts lists in Phase 6's generated
# validation_report.json files, not an exhaustive general-purpose stopword list.
_GENERIC_DESCRIPTOR_STOPWORDS = {
    "based", "call", "engineer", "engineers", "qualifies", "relevant", "small",
    "keeps", "clean", "action", "warrants", "analytical", "strong", "grasp",
    "high", "skilled", "expert", "detail", "detailed", "oriented", "meticulous",
    "thorough", "consistent", "reliable", "dedicated", "focused", "focus",
    "ensure", "ensures", "ensuring", "quick", "fast", "proper", "properly",
    "actual", "current", "overall", "general", "specific", "specifically",
    "appropriate", "necessary", "person", "someone", "everything", "something",
}

STOPWORDS = _STRUCTURAL_STOPWORDS | _GENERIC_DESCRIPTOR_STOPWORDS


def content_words_set(text: str) -> set[str]:
    """Lowercased alphabetic tokens of length >= 4, minus stopwords (unordered)."""
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOPWORDS}


def content_words_ordered(text: str) -> list[str]:
    """Same tokens, de-duplicated in first-seen order (for truncating to top-N)."""
    seen: list[str] = []
    for w in re.findall(r"[a-z]{4,}", text.lower()):
        if w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen
