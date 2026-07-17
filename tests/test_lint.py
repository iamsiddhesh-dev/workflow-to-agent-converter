"""Phase 2.2: the spec linter catches each seeded structural defect."""

from w2a.spec.lint import is_clean, lint
from w2a.spec.model import AgentSpec, Flow, TaskSpec, ToolSpec, Workflow, WorkflowSpec


def _clean_spec() -> WorkflowSpec:
    """A minimal lint-clean two-task sequential spec to mutate in each test."""
    return WorkflowSpec(
        workflow=Workflow(name="T", description="d", trigger="t", category="ops"),
        agents=[
            AgentSpec(id="a1", role="R1", goal="g", backstory_hint="b"),
            AgentSpec(id="a2", role="R2", goal="g", backstory_hint="b"),
        ],
        tasks=[
            TaskSpec(id="t1", description="parse the input file", agent_id="a1", expected_output="parsed rows"),
            TaskSpec(
                id="t2",
                description="write the report",
                agent_id="a2",
                depends_on=["t1"],
                expected_output="a markdown report",
            ),
        ],
        tools=[
            ToolSpec(id="rep", name="report writer", purpose="write the report", category="builtin", inputs="rows", outputs="report"),
        ],
        flow=Flow(pattern="sequential", edges=[("t1", "t2")]),
    )


def _codes(spec: WorkflowSpec) -> set[str]:
    return {i.code for i in lint(spec)}


def test_clean_spec_has_no_issues():
    assert lint(_clean_spec()) == []
    assert is_clean(_clean_spec())


def test_dangling_agent_id():
    s = _clean_spec()
    s.tasks[0].agent_id = "ghost"
    assert "dangling_agent_id" in _codes(s)
    assert not is_clean(s)


def test_dangling_dependency():
    s = _clean_spec()
    s.tasks[1].depends_on = ["ghost"]
    assert "dangling_dependency" in _codes(s)


def test_self_dependency():
    s = _clean_spec()
    s.tasks[1].depends_on = ["t2"]
    assert "self_dependency" in _codes(s)


def test_cyclic_dependency():
    s = _clean_spec()
    s.tasks[0].depends_on = ["t2"]  # t1<-t2 and t2<-t1
    assert "cyclic_dependency" in _codes(s)
    assert not is_clean(s)


def test_empty_expected_output():
    s = _clean_spec()
    s.tasks[0].expected_output = "   "
    assert "empty_expected_output" in _codes(s)


def test_dangling_edge():
    s = _clean_spec()
    s.flow.edges = [("t1", "ghost")]
    assert "dangling_edge" in _codes(s)


def test_orphan_task():
    s = _clean_spec()
    s.tasks.append(TaskSpec(id="t3", description="stray step", agent_id="a1", expected_output="nothing linked"))
    assert "orphan_task" in _codes(s)


def test_unused_tool():
    s = _clean_spec()
    s.tools.append(
        ToolSpec(id="weather", name="weather oracle", purpose="predict tomorrow's rainfall", category="external", inputs="location", outputs="forecast")
    )
    assert "unused_tool" in _codes(s)


def test_duplicate_agent_id():
    s = _clean_spec()
    s.agents[1].id = "a1"
    assert "duplicate_agent_id" in _codes(s)


def test_empty_spec_flags_no_agents_and_no_tasks():
    s = WorkflowSpec(
        workflow=Workflow(name="T", description="d", trigger="t", category="ops"),
        agents=[],
        tasks=[],
        tools=[],
        flow=Flow(pattern="sequential", edges=[]),
    )
    codes = _codes(s)
    assert "no_agents" in codes
    assert "no_tasks" in codes


def test_warnings_do_not_block_when_warnings_ok():
    s = _clean_spec()
    s.tasks.append(TaskSpec(id="t3", description="stray", agent_id="a1", expected_output="x"))  # orphan warning
    assert is_clean(s, warnings_ok=True)
    assert not is_clean(s, warnings_ok=False)
