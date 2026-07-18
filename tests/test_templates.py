"""Phase 3.5: golden render test. One hand-written WorkflowSpec per pattern
(``tests/golden_specs.py``) renders into a project whose every Python file
``ast.parse``s clean and whose ``crew.py`` mentions every one of the spec's
agent roles — proof the template set actually carries the spec's own design
through to code, not boilerplate.
"""

import ast

import pytest

from tests.golden_specs import GOLDEN_SPECS
from w2a.spec.model import AgentSpec, Flow, TaskSpec, Workflow, WorkflowSpec
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


# --- Phase 7.1 #1: {input} must reach at least one task description ---


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_root_tasks_interpolate_input(pattern):
    """Every pattern's crew.py must contain the literal '{input}' token in a root
    task's description — otherwise CrewAI's kickoff(inputs={'input': payload})
    is silently discarded (Phase 6 finding #1, the real-mode field trial's most
    severe bug: a stock-generated project ignores whatever you feed it)."""
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    assert "{input}" in files["crew.py"]
    root_ids = {t.id for t in spec.tasks if not t.depends_on}
    assert root_ids, f"{pattern}: golden spec has no root task to check"


def test_gap_fill_cannot_strip_the_input_marker():
    """The marker is appended by the macro from t.is_root, not baked into the
    context's 'description' field gap-fill overwrites — so even if an LLM fill
    replaces a root task's prose entirely, {input} still survives the re-render."""
    from w2a.templates.render import build_context, render_files

    spec = GOLDEN_SPECS["sequential"]
    context = build_context(spec, "sequential")
    for t in context["tasks"]:
        if t["is_root"]:
            t["description"] = "Completely different LLM-written prose with no braces at all."
    files = render_files(context, "sequential")
    assert "{input}" in files["crew.py"]


# --- Phase 7.1 #2: every task's output must be observable, not just the last ---


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_main_py_prints_every_task_output(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    main_source = files["main.py"]
    assert "_print_task_outputs" in main_source
    assert "TASK OUTPUTS" in main_source
    for task in spec.tasks:
        assert f"{task.id!r}" in main_source or task.id in main_source


# --- Phase 7.1 #9: Windows console must not choke on emoji verbose logs ---


@pytest.mark.parametrize("pattern", GOLDEN_SPECS.keys())
def test_main_py_reconfigures_stdout_encoding(pattern):
    spec = GOLDEN_SPECS[pattern]
    files = render_pattern(spec, pattern)
    assert "reconfigure(encoding=" in files["main.py"]


# --- Phase 7.1 #3: scheduled_watcher must not re-run disconnected tasks every poll ---


def _watcher_spec_with_periodic_task() -> WorkflowSpec:
    """Mirrors the real ticket_triage.json fixture: a classify->alert watch chain
    plus a weekly report task with no deps/edges — genuinely a different cadence."""
    return WorkflowSpec(
        workflow=Workflow(
            name="Ticket Watch",
            description="Watch tickets and alert on-call; separately, summarize the week.",
            trigger="Polls for new tickets on a recurring schedule.",
            category="ops",
        ),
        agents=[
            AgentSpec(id="classifier", role="Classifier", goal="classify", backstory_hint="fast"),
            AgentSpec(id="alerter", role="Alerter", goal="alert", backstory_hint="vigilant"),
            AgentSpec(id="reporter", role="Reporter", goal="report", backstory_hint="thorough"),
        ],
        tasks=[
            TaskSpec(id="classify_ticket", description="Classify the ticket.", agent_id="classifier", expected_output="a label"),
            TaskSpec(
                id="trigger_alert",
                description="Alert on-call if urgent.",
                agent_id="alerter",
                depends_on=["classify_ticket"],
                expected_output="alert sent or not",
            ),
            TaskSpec(
                id="generate_weekly_report",
                description="Summarize the week's tickets.",
                agent_id="reporter",
                expected_output="a weekly summary",
            ),
        ],
        tools=[],
        flow=Flow(pattern="watcher", edges=[("classify_ticket", "trigger_alert")]),
    )


def test_periodic_task_excluded_from_poll_crew():
    spec = _watcher_spec_with_periodic_task()
    files = render_pattern(spec, "watcher")
    crew_source = files["crew.py"]
    assert "def build_periodic_crew" in crew_source
    assert "def run_periodic" in crew_source
    # the per-poll crew must not include the disconnected weekly-report task
    build_crew_body = crew_source.split("def build_crew")[1].split("def run_once")[0]
    assert "task_generate_weekly_report" not in build_crew_body
    assert "task_classify_ticket" in build_crew_body
    assert "task_trigger_alert" in build_crew_body


def test_main_py_exposes_periodic_flag_for_watcher_with_periodic_tasks():
    spec = _watcher_spec_with_periodic_task()
    files = render_pattern(spec, "watcher")
    main_source = files["main.py"]
    assert "--periodic" in main_source
    assert "run_periodic" in main_source


def test_watcher_with_no_periodic_tasks_omits_periodic_machinery():
    spec = GOLDEN_SPECS["watcher"]  # WATCHER_SPEC: every task is chained, none disconnected
    files = render_pattern(spec, "watcher")
    assert "def build_periodic_crew" not in files["crew.py"]
    assert "run_periodic" not in files["main.py"]


# --- Phase 7.5 regression: human_checkpoint in a NON-approval pattern must
# still import MOCK_MODE (the shared macro emits `human_input=not MOCK_MODE`
# regardless of pattern). Surfaced when the 7.1 #4 translator fix made
# bug_triage render as a router — which never imported MOCK_MODE — producing
# an F821 undefined-name that only the ruff sub-check caught. ------------------


def _router_spec_with_a_human_checkpoint() -> WorkflowSpec:
    spec = GOLDEN_SPECS["router"].model_copy(deep=True)
    spec.tasks[1].human_checkpoint = True  # a branch task now needs approval
    return spec


@pytest.mark.parametrize("pattern", ["router", "sequential", "report", "watcher"])
def test_human_checkpoint_in_non_approval_pattern_imports_mock_mode(pattern):
    spec = GOLDEN_SPECS[pattern].model_copy(deep=True)
    # give the last non-root task a checkpoint (all these golden specs have >=2 tasks)
    spec.tasks[-1].human_checkpoint = True
    files = render_pattern(spec, pattern)
    crew = files["crew.py"]
    assert "human_input=not MOCK_MODE" in crew
    # MOCK_MODE must be imported, so `not MOCK_MODE` isn't an undefined name (F821)
    assert "import MOCK_MODE" in crew or ", MOCK_MODE" in crew
    ast.parse(crew)


def test_no_human_checkpoint_does_not_import_mock_mode_into_crew():
    """Conversely, a pattern with no checkpoint must NOT import MOCK_MODE into
    crew.py (it would be an unused import — ruff F401)."""
    spec = GOLDEN_SPECS["sequential"]  # onboarding: no human_checkpoint anywhere
    assert not any(t.human_checkpoint for t in spec.tasks)
    files = render_pattern(spec, "sequential")
    assert "MOCK_MODE" not in files["crew.py"]
