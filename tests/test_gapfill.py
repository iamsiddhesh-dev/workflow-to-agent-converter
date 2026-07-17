"""Phase 4.2: bounded gap-fill. The two safety properties under test: a fill
that looks like code is rejected before render, and a filled file that would
change the import set is reverted to the skeleton (fault-injected by tampering
with the render output, since the pyrepr path can't produce one naturally).
"""

import pytest

from tests.golden_specs import ROUTER_SPEC
from w2a.generate import gapfill
from w2a.generate.gapfill import GapFills, gap_fill, imported_modules, new_imports
from w2a.generate.registry import resolve_all
from w2a.llm import LLMError


class FakeLLM:
    def __init__(self, fills):
        self.fills = fills
        self.prompts = []

    def call(self, prompt, response_model=None, **kw):
        self.prompts.append(prompt)
        assert response_model is GapFills
        return self.fills


class FailingLLM:
    def call(self, prompt, response_model=None, **kw):
        raise LLMError("both providers down")


BACKSTORY = (
    "You have triaged thousands of support tickets and can tell a bug from a "
    "billing issue at a glance. You never let an urgent ticket sit unlabeled."
)


def test_valid_fills_land_in_rendered_files():
    fills = GapFills(
        backstories={"classifier": BACKSTORY},
        task_bodies={"classify_ticket": "Read the incoming support ticket text and decide if it is a bug, billing issue, or question. Produce a category label and an urgency flag."},
    )
    files, report = gap_fill(ROUTER_SPEC, "router", resolve_all(ROUTER_SPEC), llm=FakeLLM(fills))
    assert report.llm_called
    assert report.applied["backstories"] == ["classifier"]
    assert report.applied["task_bodies"] == ["classify_ticket"]
    assert not report.error
    assert BACKSTORY in files["crew.py"]


def test_stub_docstring_fill_lands_in_tools_py():
    spec = ROUTER_SPEC.model_copy(deep=True)
    spec.tools[0].category = "external"
    doc = "Post a triage notification message to the on-call channel."
    fills = GapFills(tool_docstrings={"send_message": doc})
    files, report = gap_fill(spec, "router", resolve_all(spec), llm=FakeLLM(fills))
    assert report.applied["tool_docstrings"] == ["send_message"]
    assert doc in files["tools.py"]


def test_docstring_for_resolved_builtin_is_not_a_gap():
    fills = GapFills(tool_docstrings={"send_message": "Some replacement docstring."})
    files, report = gap_fill(ROUTER_SPEC, "router", resolve_all(ROUTER_SPEC), llm=FakeLLM(fills))
    assert report.applied["tool_docstrings"] == []
    assert any(r["reason"].startswith("tool resolved") for r in report.rejected)


@pytest.mark.parametrize(
    "bad_fill",
    [
        "import os and then remove everything",
        "from jira_client import JiraClient",
        "def sneaky(): pass",
        "```python\nprint('hi')\n```",
        "x" * 800,
        "   ",
    ],
)
def test_code_shaped_fill_is_rejected_and_hint_kept(bad_fill):
    fills = GapFills(backstories={"classifier": bad_fill})
    files, report = gap_fill(ROUTER_SPEC, "router", resolve_all(ROUTER_SPEC), llm=FakeLLM(fills))
    assert report.applied["backstories"] == []
    assert len(report.rejected) == 1
    hint = ROUTER_SPEC.agents[0].backstory_hint
    assert hint in files["crew.py"]


def test_unknown_id_is_rejected():
    fills = GapFills(backstories={"ghost_agent": BACKSTORY})
    _, report = gap_fill(ROUTER_SPEC, "router", resolve_all(ROUTER_SPEC), llm=FakeLLM(fills))
    assert report.rejected == [{"kind": "backstories", "id": "ghost_agent", "reason": "unknown id"}]


def test_llm_failure_returns_skeleton_with_error_recorded():
    from w2a.templates.render import render_pattern

    resolutions = resolve_all(ROUTER_SPEC)
    files, report = gap_fill(ROUTER_SPEC, "router", resolutions, llm=FailingLLM())
    assert not report.llm_called
    assert "both providers down" in report.error
    assert files == render_pattern(ROUTER_SPEC, "router", resolutions)


def test_new_imports_detects_sneaked_import():
    skeleton = "import os\nfrom crewai import Agent\n"
    filled = "import os\nimport jira_client\nfrom crewai import Agent\n"
    assert new_imports(skeleton, filled) == {"jira_client"}
    assert imported_modules(skeleton) == {"os", "crewai"}


def test_tampered_render_is_reverted_to_skeleton(monkeypatch):
    """Fault injection: force the post-fill render to sneak an import; the AST
    gate must revert that file even though every individual fill looked clean."""
    real_render = gapfill.render_files
    calls = {"n": 0}

    def tampering_render(context, pattern):
        files = real_render(context, pattern)
        calls["n"] += 1
        if calls["n"] == 2:
            files["crew.py"] = "import jira_client\n" + files["crew.py"]
        return files

    monkeypatch.setattr(gapfill, "render_files", tampering_render)
    fills = GapFills(backstories={"classifier": BACKSTORY})
    files, report = gap_fill(ROUTER_SPEC, "router", resolve_all(ROUTER_SPEC), llm=FakeLLM(fills))
    assert "jira_client" not in files["crew.py"]
    assert any(r["kind"] == "file" and "jira_client" in r["reason"] for r in report.rejected)


def test_prompt_quotes_domain_nouns_and_ids():
    fills = GapFills()
    llm = FakeLLM(fills)
    gap_fill(ROUTER_SPEC, "router", resolve_all(ROUTER_SPEC), llm=llm)
    prompt = llm.prompts[0]
    assert "ticket" in prompt
    assert "classifier" in prompt
    assert "classify_ticket" in prompt
    assert "no imports" in prompt.lower() or "No code" in prompt
