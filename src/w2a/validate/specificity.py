"""Specificity tier: the anti-generic-scaffolding guardrail.

Static/env/exec tiers all pass on a project that runs but says nothing —
"Agent 1 processes the input, Agent 2 returns a result." That's the failure
mode this tier exists to catch.

Domain nouns are pulled only from the text gap-fill actually controls: task
descriptions (overwritten by ``task_bodies`` fills), agent backstory hints
(expanded into ``backstories`` fills), and the purpose of tools that resolved
to a MOCK_MODE stub (whose docstring comes from a ``tool_docstrings`` fill).
Deliberately excluded: ``expected_output`` and agent role/goal are rendered
verbatim from the spec regardless of gap-fill quality, and a builtin tool's
docstring is its own fixed, tested source — including any of that in the
corpus would let a badly-generic gap-fill hide behind text it never wrote.
Coverage is then scored against the *rendered* prompt text (``crew.py`` +
``tools.py``), so this is exactly "did the fill keep the spec's own nouns, or
drift into boilerplate" — never a tautology against itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from w2a.spec.model import WorkflowSpec
from w2a.templates.render import _content_words

DEFAULT_THRESHOLD = 0.6
DEFAULT_PROMPT_FILES = ("crew.py", "tools.py")


@dataclass
class SpecificityReport:
    ok: bool
    score: float
    threshold: float
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    verdict: str = "specific"

    def __str__(self) -> str:
        lines = [
            f"specificity tier: {'pass' if self.ok else 'fail'} "
            f"({self.score:.2f} >= {self.threshold} required) — verdict: {self.verdict}"
        ]
        if self.missing:
            lines.append(f"  missing concepts: {self.missing}")
        return "\n".join(lines)


def domain_nouns(spec: WorkflowSpec, resolutions: dict | None = None) -> list[str]:
    """The vocabulary gap-fill is actually responsible for keeping — not the whole spec."""
    if resolutions is None:
        from w2a.generate.registry import resolve_all

        resolutions = resolve_all(spec)

    corpus_parts = [t.description for t in spec.tasks]
    corpus_parts += [a.backstory_hint for a in spec.agents]
    for tl in spec.tools:
        resolution = resolutions.get(tl.id)
        if getattr(resolution, "source", None) is None:  # unresolved -> stub docstring is a gap
            corpus_parts.append(f"{tl.name} {tl.purpose}")
    return _content_words(" ".join(corpus_parts))


def check_specificity(
    spec: WorkflowSpec,
    files: dict[str, str],
    threshold: float = DEFAULT_THRESHOLD,
    prompt_files: tuple[str, ...] = DEFAULT_PROMPT_FILES,
    resolutions: dict | None = None,
) -> SpecificityReport:
    nouns = domain_nouns(spec, resolutions)
    if not nouns:
        return SpecificityReport(ok=True, score=1.0, threshold=threshold, verdict="specific (no domain nouns to check)")

    haystack = " ".join(files.get(name, "") for name in prompt_files).lower()
    covered = [n for n in nouns if n in haystack]
    missing = [n for n in nouns if n not in haystack]
    score = len(covered) / len(nouns)
    ok = score >= threshold
    return SpecificityReport(
        ok=ok,
        score=score,
        threshold=threshold,
        covered=covered,
        missing=missing,
        verdict="specific" if ok else "generic scaffolding",
    )


def run_specificity_tier(
    project_dir: Path,
    spec: WorkflowSpec,
    threshold: float = DEFAULT_THRESHOLD,
    prompt_files: tuple[str, ...] = DEFAULT_PROMPT_FILES,
    resolutions: dict | None = None,
) -> SpecificityReport:
    files = {
        name: (project_dir / name).read_text(encoding="utf-8")
        for name in prompt_files
        if (project_dir / name).exists()
    }
    return check_specificity(spec, files, threshold=threshold, prompt_files=prompt_files, resolutions=resolutions)
