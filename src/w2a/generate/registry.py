"""Closed tool registry: spec ToolSpecs resolve to real builtins or explicit stubs.

This is one of the two decisions that separate "actually runnable" from
"plausible-looking" (the other is the deterministic-template/LLM-gap boundary):
the generator has NO code path that emits an import or class name outside this
registry and the Phase-3 templates. A resolved tool is emitted as the verbatim,
tested source of a builtin from ``w2a/templates/builtin_tools.py``; everything
else becomes a MOCK_MODE stub with a TODO — never an invented API.

Matching is deliberately conservative in Phase 4 (exact normalized name, or a
keyword-overlap score with a unique best match); paraphrase hardening ("ping
the team channel" -> send_message) is Phase 7's job. ``category == "external"``
tools always stub: mapping a named third-party system onto a stand-in is a
product decision the user should see in the manifest, not a silent guess.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from w2a.spec.model import ToolSpec, WorkflowSpec

BUILTIN_TOOLS_PATH = Path(__file__).parent.parent / "templates" / "builtin_tools.py"

_BUILTIN_IMPORTS: dict[str, tuple[str, ...]] = {
    "read_file": ("from pathlib import Path",),
    "write_file": ("from pathlib import Path",),
    "http_get": ("import requests",),
    "parse_csv": ("import csv", "from pathlib import Path"),
    "write_markdown_report": ("import io", "from pathlib import Path"),
    "send_message": ("import json", "import time", "from pathlib import Path"),
}

_BUILTIN_KEYWORDS: dict[str, set[str]] = {
    "read_file": {"read", "file", "open", "load", "contents", "retrieve", "grab"},
    "write_file": {"write", "file", "save", "store", "persist", "record"},
    "http_get": {"http", "get", "fetch", "url", "request", "api", "download", "web", "endpoint", "call"},
    "parse_csv": {"csv", "parse", "spreadsheet", "rows", "table", "excel", "columns", "sheet"},
    "write_markdown_report": {"markdown", "report", "document", "format", "summary", "write", "compile", "writeup", "doc"},
    "send_message": {
        "send", "message", "notify", "notification", "ping", "channel", "alert", "post",
        "slack", "email", "chat", "note", "broadcast", "tell", "inform", "page",
    },
}

MIN_KEYWORD_HITS = 2


@dataclass(frozen=True)
class BuiltinTool:
    """A real, tested tool whose source is emitted verbatim into generated tools.py."""

    name: str
    doc: str
    source: str
    imports: tuple[str, ...]


@dataclass(frozen=True)
class StubPlan:
    """An unresolved tool: emitted as an explicit MOCK_MODE stub with a TODO."""

    tool_id: str
    name: str
    purpose: str
    outputs: str
    reason: str


@lru_cache(maxsize=1)
def builtins() -> dict[str, BuiltinTool]:
    """Extract each @tool function's source (decorator included) from builtin_tools.py."""
    text = BUILTIN_TOOLS_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)
    out: dict[str, BuiltinTool] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
        source = "\n".join(lines[start : node.end_lineno])
        out[node.name] = BuiltinTool(
            name=node.name,
            doc=ast.get_docstring(node) or "",
            source=source,
            imports=_BUILTIN_IMPORTS[node.name],
        )
    return out


def _tokens(text: str) -> set[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {w for w in re.findall(r"[a-z]+", spaced.lower()) if len(w) >= 3}


def resolve(tool_spec: ToolSpec) -> BuiltinTool | StubPlan:
    """Match one spec tool against the builtins, or plan an explicit stub."""

    def stub(reason: str) -> StubPlan:
        return StubPlan(
            tool_id=tool_spec.id,
            name=tool_spec.name,
            purpose=tool_spec.purpose,
            outputs=tool_spec.outputs,
            reason=reason,
        )

    if tool_spec.category == "external":
        return stub("external system — kept as a stub until real credentials/wiring exist")

    registry = builtins()
    name_tokens = _tokens(tool_spec.name)
    for builtin in registry.values():
        if name_tokens and name_tokens == _tokens(builtin.name):
            return builtin

    mention = _tokens(tool_spec.name) | _tokens(tool_spec.purpose)
    scores = {name: len(mention & kws) for name, kws in _BUILTIN_KEYWORDS.items()}
    best = max(scores.values())
    winners = [name for name, s in scores.items() if s == best]
    if best >= MIN_KEYWORD_HITS and len(winners) == 1:
        return registry[winners[0]]
    if best >= MIN_KEYWORD_HITS:
        return stub(f"ambiguous match between builtins {winners} — surfaced instead of guessed")
    return stub("no builtin matched by name or purpose")


def resolve_all(spec: WorkflowSpec) -> dict[str, BuiltinTool | StubPlan]:
    """Resolve every tool in the spec, keyed by tool id."""
    return {t.id: resolve(t) for t in spec.tools}
