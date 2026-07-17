"""Phase 3.5: golden render test. One hand-written WorkflowSpec per pattern
(``tests/golden_specs.py``) renders into a project whose every Python file
``ast.parse``s clean and whose ``crew.py`` mentions every one of the spec's
agent roles — proof the template set actually carries the spec's own design
through to code, not boilerplate.
"""

import ast

import pytest

from tests.golden_specs import GOLDEN_SPECS
from w2a.templates.render import OUTPUT_FILES, render_pattern


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_renders_all_output_files(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    assert set(files.keys()) == set(OUTPUT_FILES)
    for content in files.values():
        assert content.strip()


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_python_files_parse_clean(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    for filename, content in files.items():
        if filename.endswith(".py"):
            try:
                ast.parse(content)
            except SyntaxError as exc:  # pragma: no cover - failure path prints the bad file
                pytest.fail(f"{pattern}/{filename} failed to parse: {exc}\n---\n{content}")


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_crew_py_mentions_every_agent_role(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    crew_source = files["crew.py"]
    for agent in spec.agents:
        assert agent.role in crew_source, f"{pattern}: agent role {agent.role!r} missing from crew.py"


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_crew_py_mentions_every_task_id(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    crew_source = files["crew.py"]
    for task in spec.tasks:
        assert f"task_{task.id}" in crew_source


def test_router_pattern_uses_conditional_task():
    files = render_pattern(GOLDEN_SPECS["router"], "router")
    assert "ConditionalTask" in files["crew.py"]


def test_approval_pattern_sets_human_input_on_checkpoint_task():
    files = render_pattern(GOLDEN_SPECS["approval"], "approval")
    assert "human_input=True" in files["crew.py"]


def test_report_pattern_writes_output_file_on_leaf_task():
    files = render_pattern(GOLDEN_SPECS["report"], "report")
    assert "output_file=" in files["crew.py"]


def test_watcher_pattern_exposes_run_once_and_poll_loop():
    files = render_pattern(GOLDEN_SPECS["watcher"], "watcher")
    assert "def run_once(" in files["crew.py"]
    assert "--interval" in files["main.py"]
    assert "--once" in files["main.py"]


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_tool_stubs_have_todo_and_mock_mode(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    if spec.tools:
        assert "MOCK_MODE" in files["tools.py"]
        assert "# TODO: connect real" in files["tools.py"]


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_readme_names_the_workflow(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    assert spec.workflow.name in files["README.md"]


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_requirements_and_env_example_render(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    assert "crewai" in files["requirements.txt"]
    assert "GEMINI_API_KEY" in files[".env.example"]
