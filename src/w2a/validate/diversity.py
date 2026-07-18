"""Diversity check: two different workflows must not generate near-identical prose.

The specificity tier (``validate/specificity.py``) proves a project's prompts
cover *its own* domain vocabulary. That is necessary but not sufficient — a
gap-fill that always writes the same generic-but-plausible paragraph regardless
of the spec could still pass specificity for each project individually (its own
domain nouns technically appear) while being boilerplate across the fleet. This
check compares the LLM-authored prose (agent backstories, task descriptions,
tool docstrings — the exact three gap-fill kinds) from two *different*
generated projects and fails if they overlap too much at the phrase level.

Deliberately scoped to prose, not the whole ``crew.py`` file: the templated
scaffolding (``Agent(...)``, ``Task(...)``, ``Crew(...)``) is identical across
every project sharing a pattern by design — that is reuse, not genericness, and
diffing whole files would produce false positives on every same-pattern pair.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

DEFAULT_MAX_OVERLAP = 0.5
DEFAULT_NGRAM_SIZE = 3

_PROSE_KWARGS = {"description", "backstory", "goal", "role"}
_PROSE_CALLS = {"Agent", "Task", "ConditionalTask"}


def extract_prompt_text(source: str) -> str:
    """Pull every prose string literal passed to an Agent/Task/ConditionalTask
    call's description/backstory/goal/role kwarg out of a rendered crew.py."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    parts: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _PROSE_CALLS):
            continue
        for kw in node.keywords:
            if kw.arg in _PROSE_KWARGS and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                parts.append(kw.value.value)
    return " ".join(parts)


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z]+", text.lower())
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass
class DiversityReport:
    ok: bool
    overlap: float
    threshold: float
    shared_ngrams: int
    compared_ngrams: int

    def __str__(self) -> str:
        verdict = "pass" if self.ok else "fail"
        return (
            f"diversity tier: {verdict} (overlap {self.overlap:.2f} <= {self.threshold} required, "
            f"{self.shared_ngrams}/{self.compared_ngrams} shared {DEFAULT_NGRAM_SIZE}-grams)"
        )


def check_diversity(
    prose_a: str, prose_b: str, threshold: float = DEFAULT_MAX_OVERLAP, n: int = DEFAULT_NGRAM_SIZE
) -> DiversityReport:
    """Overlap-coefficient (shared / smaller set) rather than Jaccard: two
    projects' prose corpora are rarely the same length, and what matters is how
    much of the *smaller* one is duplicated in the other, not diluted by size."""
    grams_a, grams_b = _ngrams(prose_a, n), _ngrams(prose_b, n)
    if not grams_a or not grams_b:
        return DiversityReport(ok=True, overlap=0.0, threshold=threshold, shared_ngrams=0, compared_ngrams=0)
    shared = grams_a & grams_b
    smaller = min(len(grams_a), len(grams_b))
    overlap = len(shared) / smaller
    return DiversityReport(
        ok=overlap <= threshold, overlap=overlap, threshold=threshold,
        shared_ngrams=len(shared), compared_ngrams=smaller,
    )


def check_project_diversity(
    crew_py_a: str, crew_py_b: str, threshold: float = DEFAULT_MAX_OVERLAP, n: int = DEFAULT_NGRAM_SIZE
) -> DiversityReport:
    """Convenience wrapper: extract prose from two rendered crew.py files and compare."""
    return check_diversity(extract_prompt_text(crew_py_a), extract_prompt_text(crew_py_b), threshold=threshold, n=n)
