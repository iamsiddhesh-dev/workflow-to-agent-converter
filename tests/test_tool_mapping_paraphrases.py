"""Phase 7.3: tool-mapping hardening.

15 paraphrased tool mentions a real user might type, none using the builtin's
own canonical name verbatim. The bar (DETAILED_PLAN.md 7.3 / PLAN.md Phase 7
DoD): >=90% "correct or asked" — every case must either resolve to the *right*
builtin, or come back as an explicit stub (never a silent wrong guess). A stub
counts as correct whenever the mention genuinely doesn't match a builtin
capability (a third-party system, or a tie between two plausible builtins) —
that is the fuzzy matcher doing its job, not failing it.
"""

from __future__ import annotations

from w2a.generate.registry import BuiltinTool, StubPlan, resolve
from w2a.spec.model import ToolSpec


def _tool(name: str, purpose: str, category: str = "builtin") -> ToolSpec:
    return ToolSpec(id="t", name=name, purpose=purpose, category=category, inputs="x", outputs="y")


# (name, purpose, category) -> expected: a builtin name string, "stub_ambiguous", or "stub_external"
CASES: list[tuple[str, str, str, str]] = [
    ("file reader", "grab the contents of the input file", "builtin", "read_file"),
    ("ticket loader", "open and read the ticket file from disk", "builtin", "read_file"),
    ("results saver", "save the results to a file for later", "builtin", "write_file"),
    ("output writer", "write the output to a file on disk", "builtin", "write_file"),
    ("api caller", "fetch data from the external API endpoint", "builtin", "http_get"),
    ("rate fetcher", "download the latest exchange rates from the web", "builtin", "http_get"),
    ("sheet parser", "parse the uploaded spreadsheet into rows", "builtin", "parse_csv"),
    ("export converter", "convert the CSV export into structured columns", "builtin", "parse_csv"),
    ("summary compiler", "compile a written summary as a markdown doc", "builtin", "write_markdown_report"),
    ("weekly writeup", "put together a report write-up of what happened this week", "builtin", "write_markdown_report"),
    ("channel pinger", "ping the team channel", "builtin", "send_message"),
    ("email notifier", "send an email notification to the team", "builtin", "send_message"),
    ("generic writer", "write the file report", "builtin", "stub_ambiguous"),
    ("bug tracker", "file it in our bug tracker", "external", "stub_external"),
    ("diff checker", "check the diff for risky changes", "external", "stub_external"),
]


def test_fifteen_paraphrases_at_least_90pct_correct_or_asked():
    results = []
    for name, purpose, category, expected in CASES:
        resolved = resolve(_tool(name, purpose, category))
        if expected == "stub_ambiguous":
            ok = isinstance(resolved, StubPlan) and "ambiguous" in resolved.reason
        elif expected == "stub_external":
            ok = isinstance(resolved, StubPlan)
        else:
            ok = isinstance(resolved, BuiltinTool) and resolved.name == expected
        results.append((name, expected, resolved, ok))

    failures = [(name, expected, resolved) for name, expected, resolved, ok in results if not ok]
    correct = len(results) - len(failures)
    rate = correct / len(results)
    assert rate >= 0.9, f"only {correct}/{len(results)} correct-or-asked ({rate:.0%}); failures: {failures}"


def test_no_case_silently_mismaps_to_the_wrong_builtin():
    """The stricter, more important assertion: even the 10% slack above must never
    be spent on a *wrong* builtin match — only on an over-cautious stub."""
    for name, purpose, category, expected in CASES:
        if expected.startswith("stub"):
            continue
        resolved = resolve(_tool(name, purpose, category))
        if isinstance(resolved, BuiltinTool):
            assert resolved.name == expected, f"{name!r} mismapped to {resolved.name}, expected {expected}"


def test_ambiguous_tie_is_surfaced_not_guessed():
    resolved = resolve(_tool("generic writer", "write the file report", "builtin"))
    assert isinstance(resolved, StubPlan)
    assert "ambiguous" in resolved.reason
