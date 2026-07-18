"""Bounded LLM gap-fill: the LLM writes prose into fixed slots, never code.

The other half of the central reliability decision (see ``registry.py``):
structure is deterministic template output, and the LLM only fills three gap
kinds — agent backstories (expanding ``backstory_hint``), task prompt bodies
(expanding the task description), and stub-tool docstrings. Every fill lands
in the rendered files through the ``pyrepr`` filter, so it can only ever be a
string literal — and the AST gate below re-checks that anyway: a filled file
must still parse and must import exactly what the skeleton imported. A fill
that fails any check is dropped and the skeleton text kept; gap-fill degrades,
it never breaks a project.
"""

from __future__ import annotations

import ast
import copy
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from w2a.llm import LLM, LLMError
from w2a.spec.model import Pattern, WorkflowSpec, human_summary
from w2a.templates.render import _content_words, build_context, render_files

logger = logging.getLogger(__name__)

MAX_FILL_CHARS = 700

_CODE_PATTERNS = (
    re.compile(r"```"),
    re.compile(r"\b(?:from\s+[\w.]+\s+)?import\s+[\w.]+"),
    re.compile(r"\bdef\s+\w+\s*\("),
    re.compile(r"\bclass\s+\w+\s*[(:]"),
    re.compile(r"\blambda\b|__import__|\bexec\(|\beval\("),
)


class GapFills(BaseModel):
    """The LLM's answer: prose fills keyed by the spec's own ids."""

    backstories: dict[str, str] = Field(
        default_factory=dict,
        description="agent_id -> a 2-4 sentence CrewAI backstory expanding that agent's one-line hint.",
    )
    task_bodies: dict[str, str] = Field(
        default_factory=dict,
        description="task_id -> a 2-4 sentence task prompt body expanding the task description, naming its concrete inputs and output artifact.",
    )
    tool_docstrings: dict[str, str] = Field(
        default_factory=dict,
        description="tool_id -> a 1-2 sentence docstring for the stub tool, grounded in its declared purpose.",
    )


@dataclass
class GapFillReport:
    applied: dict[str, list[str]] = field(default_factory=lambda: {"backstories": [], "task_bodies": [], "tool_docstrings": []})
    rejected: list[dict] = field(default_factory=list)
    llm_called: bool = False
    error: str | None = None


def imported_modules(source: str) -> set[str]:
    """Every module name imported by the source, as written (dotted names kept)."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
    return out


def new_imports(skeleton_source: str, filled_source: str) -> set[str]:
    """Imports present in the filled file but not the skeleton — must be empty."""
    return imported_modules(filled_source) - imported_modules(skeleton_source)


def _fill_ok(text: str) -> str | None:
    """Return a rejection reason, or None if the fill is acceptable prose."""
    if not isinstance(text, str) or not text.strip():
        return "empty fill"
    if len(text) > MAX_FILL_CHARS:
        return f"fill exceeds {MAX_FILL_CHARS} chars"
    for pattern in _CODE_PATTERNS:
        if pattern.search(text):
            return f"fill looks like code (matched {pattern.pattern!r})"
    return None


def _domain_nouns(spec: WorkflowSpec) -> list[str]:
    corpus = " ".join(
        [spec.workflow.name, spec.workflow.description, spec.workflow.trigger]
        + [t.description for t in spec.tasks]
        + [f"{tl.name} {tl.purpose}" for tl in spec.tools]
    )
    return _content_words(corpus)[:20]


def _build_prompt(spec: WorkflowSpec, stub_tool_ids: list[str], retry_hint: str = "") -> str:
    agent_ids = ", ".join(a.id for a in spec.agents)
    task_ids = ", ".join(t.id for t in spec.tasks)
    tool_line = (
        f"- tool_docstrings: one entry per tool id ({', '.join(stub_tool_ids)}): "
        "a 1-2 sentence docstring grounded in the tool's declared purpose.\n"
        if stub_tool_ids
        else "- tool_docstrings: leave empty, every tool already resolved to a real implementation.\n"
    )
    return (
        "You are filling narrow prose gaps in an already-generated CrewAI project. "
        "The code and structure are fixed and none of your text will be executed — "
        "you write short prose only.\n\n"
        f"Workflow spec:\n{human_summary(spec)}\n\n"
        f"Ground every fill in the spec's own domain nouns where relevant: {', '.join(_domain_nouns(spec))}\n\n"
        "Write:\n"
        f"- backstories: one entry per agent id ({agent_ids}): a 2-4 sentence CrewAI "
        "backstory expanding that agent's hint into a persona with relevant expertise.\n"
        f"- task_bodies: one entry per task id ({task_ids}): a 2-4 sentence task prompt "
        "body expanding the task description, naming the concrete inputs it receives "
        "and the artifact it must produce, per the spec.\n"
        f"{tool_line}\n"
        "Rules: plain prose only. No code, no imports, no markdown fences, and no tool, "
        "system, or agent names beyond those in the spec. Return ONLY the JSON object."
        + (f"\n\n{retry_hint}" if retry_hint else "")
    )


def _apply_fills(context: dict, fills: GapFills, report: GapFillReport) -> dict:
    filled = copy.deepcopy(context)

    def take(kind: str, provided: dict[str, str], entries: list[dict], target_field: str) -> None:
        by_id = {e["id"]: e for e in entries}
        for item_id, text in provided.items():
            entry = by_id.get(item_id)
            if entry is None:
                report.rejected.append({"kind": kind, "id": item_id, "reason": "unknown id"})
                continue
            if kind == "tool_docstrings" and entry.get("resolved"):
                report.rejected.append({"kind": kind, "id": item_id, "reason": "tool resolved to a builtin — docstring not a gap"})
                continue
            reason = _fill_ok(text)
            if reason is not None:
                report.rejected.append({"kind": kind, "id": item_id, "reason": reason})
                continue
            entry[target_field] = text.strip()
            report.applied[kind].append(item_id)

    take("backstories", fills.backstories, filled["agents"], "backstory")
    take("task_bodies", fills.task_bodies, filled["tasks"], "description")
    take("tool_docstrings", fills.tool_docstrings, filled["tools"], "purpose")
    return filled


def gap_fill(
    spec: WorkflowSpec,
    pattern: Pattern,
    resolutions: dict | None = None,
    llm: LLM | None = None,
    retry_hint: str = "",
) -> tuple[dict[str, str], GapFillReport]:
    """Render the skeleton, ask the LLM for fills, and re-render behind the AST gate.

    Always returns a usable file set: on any LLM failure the untouched skeleton
    comes back with the failure recorded in the report. ``retry_hint`` lets a
    caller (the specificity repair strategy) ask for a second, better-grounded
    pass without changing the gap kinds or the AST gate.
    """
    context = build_context(spec, pattern, resolutions)
    skeleton = render_files(context, pattern)
    report = GapFillReport()

    stub_tool_ids = [t["id"] for t in context["tools"] if not t["resolved"]]
    try:
        llm = llm or LLM()
        fills = llm.call(_build_prompt(spec, stub_tool_ids, retry_hint), response_model=GapFills)
        report.llm_called = True
    except LLMError as exc:
        report.error = str(exc)
        logger.warning("gap-fill skipped, keeping skeleton: %s", exc)
        return skeleton, report

    filled_context = _apply_fills(context, fills, report)
    filled = render_files(filled_context, pattern)

    for filename, content in list(filled.items()):
        if not filename.endswith(".py"):
            continue
        try:
            sneaked = new_imports(skeleton[filename], content)
        except SyntaxError as exc:
            report.rejected.append({"kind": "file", "id": filename, "reason": f"filled file no longer parses: {exc}"})
            filled[filename] = skeleton[filename]
            continue
        if sneaked:
            report.rejected.append({"kind": "file", "id": filename, "reason": f"fill introduced imports {sorted(sneaked)}"})
            filled[filename] = skeleton[filename]

    return filled, report
