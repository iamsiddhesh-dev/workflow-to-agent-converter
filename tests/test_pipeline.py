"""Phase 4.4: pipeline wiring. The acceptance case from the plan: a
lint-failing spec lands as a structured pipeline error in state, not a
traceback — plus the happy path and the gap-fill-degrades path, all offline
via fake LLMs.
"""


from tests.golden_specs import ROUTER_SPEC
from w2a.generate.gapfill import GapFills
from w2a.llm import LLMError
from w2a.pipeline.graph import run_pipeline
from w2a.spec.model import TaskSpec


class FakeLLM:
    """Answers the gap-fill call; anything else in the offline tests would be a bug."""

    def call(self, prompt, response_model=None, **kw):
        assert response_model is GapFills, f"unexpected LLM call with {response_model}"
        return GapFills(
            backstories={
                "classifier": "You have triaged thousands of tickets and can spot a bug report instantly."
            }
        )


class DownLLM:
    def call(self, prompt, response_model=None, **kw):
        raise LLMError("simulated outage")


def test_happy_path_generates_project(tmp_path):
    state = run_pipeline(spec=ROUTER_SPEC, llm=FakeLLM(), out_root=str(tmp_path))
    assert state["errors"] == []
    assert state["selection"].pattern == "router"
    assert state["selection"].source == "deterministic"
    assert "thousands of tickets" in state["files"]["crew.py"]
    project = state["write_result"].project_dir
    assert (project / "manifest.json").exists()
    assert (project / "crew.py").exists()
    assert state["manifest"]["llm_calls"] == ["gap_fill"]
    assert state["manifest"]["pattern"]["selected"] == "router"


def test_lint_failing_spec_is_a_structured_error_not_a_traceback(tmp_path):
    broken = ROUTER_SPEC.model_copy(deep=True)
    broken.tasks.append(
        TaskSpec(
            id="orphan",
            description="A task owned by nobody.",
            agent_id="ghost_agent",
            expected_output="Nothing good.",
        )
    )
    state = run_pipeline(spec=broken, llm=FakeLLM(), out_root=str(tmp_path))
    assert state["errors"], "lint errors should accumulate in state"
    assert all(e.node == "lint" for e in state["errors"])
    assert any("ghost_agent" in e.message for e in state["errors"])
    assert "files" not in state
    assert not any(tmp_path.iterdir()), "nothing may be written after a lint failure"


def test_llm_outage_ships_skeleton_with_warning(tmp_path):
    state = run_pipeline(spec=ROUTER_SPEC, llm=DownLLM(), out_root=str(tmp_path))
    assert state["errors"] == []
    assert any("gap_fill" in w for w in state["warnings"])
    assert state["files"] == state["skeleton"]
    assert (state["write_result"].project_dir / "crew.py").exists()
    assert state["manifest"]["gap_fill"]["error"]
    assert state["manifest"]["llm_calls"] == []


def test_empty_source_is_a_parse_error(tmp_path):
    state = run_pipeline(source="   ", llm=FakeLLM(), out_root=str(tmp_path))
    assert len(state["errors"]) == 1
    assert state["errors"][0].node == "parse"
    assert state["errors"][0].kind == "EmptyInput"


def test_foreign_directory_becomes_a_write_error(tmp_path):
    foreign = tmp_path / "support_ticket_triage"
    foreign.mkdir()
    (foreign / "precious.py").write_text("mine", encoding="utf-8")
    state = run_pipeline(spec=ROUTER_SPEC, llm=FakeLLM(), out_root=str(tmp_path))
    assert any(e.node == "write" and e.kind == "WriterError" for e in state["errors"])
    assert (foreign / "precious.py").read_text(encoding="utf-8") == "mine"


def test_rerun_is_a_no_op(tmp_path):
    run_pipeline(spec=ROUTER_SPEC, llm=DownLLM(), out_root=str(tmp_path))
    state = run_pipeline(spec=ROUTER_SPEC, llm=DownLLM(), out_root=str(tmp_path))
    assert state["errors"] == []
    assert state["write_result"].no_op
