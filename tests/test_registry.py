"""Phase 4.1: closed tool registry. The acceptance case from the plan — a spec
inventing 'SlackNotifierTool' must resolve to an explicit stub with a TODO,
never a fabricated import — plus exact/fuzzy matching against the 6 builtins.
"""

import ast

import pytest

from tests.golden_specs import ROUTER_SPEC
from w2a.generate.registry import BuiltinTool, StubPlan, builtins, resolve, resolve_all
from w2a.spec.model import ToolSpec
from w2a.templates.render import render_pattern


def _tool(name, purpose, category="builtin", tool_id="t1"):
    return ToolSpec(
        id=tool_id, name=name, purpose=purpose, category=category,
        inputs="text", outputs="text",
    )


def test_all_six_builtins_extracted_with_source():
    reg = builtins()
    assert set(reg) == {
        "read_file", "write_file", "http_get", "parse_csv",
        "write_markdown_report", "send_message",
    }
    for b in reg.values():
        assert f"def {b.name}" in b.source
        assert b.source.lstrip().startswith("@tool")
        ast.parse(b.source)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("send message", "send_message"),
        ("Send Message", "send_message"),
        ("read file", "read_file"),
        ("write markdown report", "write_markdown_report"),
        ("parse CSV", "parse_csv"),
        ("HTTP GET", "http_get"),
    ],
)
def test_exact_normalized_name_match(name, expected):
    result = resolve(_tool(name, "does the thing"))
    assert isinstance(result, BuiltinTool)
    assert result.name == expected


def test_fuzzy_purpose_match():
    result = resolve(_tool("report writer", "write the weekly summary as a markdown document"))
    assert isinstance(result, BuiltinTool)
    assert result.name == "write_markdown_report"


def test_invented_external_tool_becomes_stub():
    result = resolve(_tool("SlackNotifierTool", "post triage results to slack", category="external"))
    assert isinstance(result, StubPlan)
    assert "external" in result.reason


def test_unmatchable_builtin_category_tool_becomes_stub():
    result = resolve(_tool("quantum flux capacitor", "recalibrate the flux"))
    assert isinstance(result, StubPlan)


def test_invented_tool_renders_as_stub_never_a_fabricated_import():
    spec = ROUTER_SPEC.model_copy(deep=True)
    spec.tools.append(
        _tool("SlackNotifierTool", "post triage results to slack", category="external", tool_id="slack_notifier")
    )
    files = render_pattern(spec, "router", resolve_all(spec))
    tools_src = files["tools.py"]
    assert "# TODO: connect real SlackNotifierTool" in tools_src
    assert "MOCK_MODE" in tools_src
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(ast.parse(tools_src))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "slack" not in imported
    assert imported <= {"__future__", "pathlib", "json", "time", "crewai", "config", "csv", "io", "requests"}


def test_duplicate_resolutions_emit_builtin_source_once():
    spec = ROUTER_SPEC.model_copy(deep=True)
    spec.tools.append(
        _tool("send message", "also ping the billing channel", tool_id="ping_billing")
    )
    files = render_pattern(spec, "router", resolve_all(spec))
    assert files["tools.py"].count("def send_message(") == 1
    assert files["tools.py"].count("ALL_TOOLS = [send_message]") == 1


def test_resolve_all_keys_by_tool_id():
    res = resolve_all(ROUTER_SPEC)
    assert set(res) == {t.id for t in ROUTER_SPEC.tools}
    assert isinstance(res["send_message"], BuiltinTool)
