"""Phase 7.5: chaos tests for the converter pipeline itself.

Every node is wrapped by ``pipeline.graph._guard``, which turns any exception
into a structured ``PipelineError`` in state rather than letting it propagate
as a traceback (see ``pipeline/state.py``). These tests prove that guarantee
holds for the specific chaos scenarios DETAILED_PLAN.md Phase 7.5 calls out:
an LLM outage/timeout mid-generation, malformed structured output at a node,
and an empty template render — plus that a pipeline state built around an
already-parsed spec is a genuine resumable checkpoint: a downstream failure
doesn't force redoing the (expensive, real) translation step.
"""

from __future__ import annotations


from tests.golden_specs import ROUTER_SPEC
from w2a.generate.gapfill import GapFills
from w2a.llm import LLMError, LLMResponseError
from w2a.pipeline.graph import run_pipeline


class _RaisingLLM:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.calls = 0

    def call(self, prompt, response_model=None, **kw):
        self.calls += 1
        raise self.exc


class _PoisonLLM:
    """Raises if called at all — proves a code path never touches the LLM."""

    def call(self, *a, **kw):
        raise AssertionError("this pipeline run should not have called the LLM")


# --- LLM outage / timeout mid-generation -------------------------------------


def test_llm_timeout_during_translate_is_a_clean_parse_error(tmp_path):
    timeout_exc = LLMError("Both providers failed: Gemini timed out, Groq timed out.")
    state = run_pipeline(source="automate our thing", llm=_RaisingLLM(timeout_exc), out_root=str(tmp_path))

    assert len(state["errors"]) == 1
    assert state["errors"][0].node == "parse"
    assert "timed out" in state["errors"][0].message
    assert "files" not in state
    assert "manifest" not in state
    assert not any(tmp_path.iterdir()), "nothing may be written after a parse-stage outage"


def test_llm_timeout_during_pattern_fallback_degrades_to_deterministic(tmp_path):
    """select_pattern's LLM fallback failing must not kill the run — it falls
    back to the declared pattern with a warning (graph.py's select_node already
    has this except-clause; this proves it under a live pipeline invocation)."""
    ambiguous_spec = ROUTER_SPEC.model_copy(deep=True)
    ambiguous_spec.flow.pattern = "sequential"  # declared pattern won't match the router-shaped graph

    class _FallbackDownLLM:
        def call(self, prompt, response_model=None, **kw):
            if response_model is GapFills:
                return GapFills()
            raise LLMError("provider timeout during pattern fallback")

    state = run_pipeline(spec=ambiguous_spec, llm=_FallbackDownLLM(), out_root=str(tmp_path))

    assert state["errors"] == []
    assert state["selection"].source == "deterministic"
    assert state["selection"].pattern == "sequential"
    assert any("select_pattern" in w for w in state["warnings"])


# --- Malformed structured output at a node -----------------------------------


def test_malformed_output_exhausted_during_translate_is_a_clean_parse_error(tmp_path):
    malformed_exc = LLMResponseError("Structured output failed after 3 attempts: invalid JSON.", raw_output="{ not json")
    state = run_pipeline(source="automate our thing", llm=_RaisingLLM(malformed_exc), out_root=str(tmp_path))

    assert len(state["errors"]) == 1
    assert state["errors"][0].node == "parse"
    assert state["errors"][0].kind == "LLMResponseError"
    assert not any(tmp_path.iterdir())


def test_malformed_output_during_gap_fill_ships_skeleton_with_warning(tmp_path):
    malformed_exc = LLMResponseError("Structured output failed after 3 attempts: invalid JSON.", raw_output="{ not json")
    state = run_pipeline(spec=ROUTER_SPEC, llm=_RaisingLLM(malformed_exc), out_root=str(tmp_path))

    assert state["errors"] == []  # gap-fill degrades, it never breaks the run
    assert any("gap_fill" in w for w in state["warnings"])
    assert state["files"] == state["skeleton"]
    assert (state["write_result"].project_dir / "crew.py").exists()


# --- Empty template render ----------------------------------------------------


def test_empty_template_render_is_a_clean_render_error(tmp_path, monkeypatch):
    from w2a.pipeline import graph as graph_module

    def _blank_render(spec, pattern, resolutions):
        files = {"crew.py": "", "tools.py": "# stub\n"}
        return files

    monkeypatch.setattr(graph_module, "render_pattern", _blank_render)

    state = run_pipeline(spec=ROUTER_SPEC, llm=_PoisonLLM(), out_root=str(tmp_path))

    assert len(state["errors"]) == 1
    assert state["errors"][0].node == "render"
    assert "crew.py" in state["errors"][0].message
    assert "files" not in state
    assert not any(tmp_path.iterdir()), "nothing may be written after a render-stage failure"


# --- Resumable checkpoint: a downstream failure doesn't force re-translation --


def test_pipeline_state_around_a_parsed_spec_is_a_resumable_checkpoint(tmp_path):
    """A run that fails downstream (a foreign directory conflict at write) still
    holds a validated, already-parsed spec. Re-invoking the pipeline with that
    same spec object (as the CLI's clarify loop and repair-loop callers already
    do) must not re-run translation — proven here with an LLM that asserts if
    called at all, only gap-fill (GapFills) responses are ever legitimate."""
    foreign = tmp_path / "support_ticket_triage"
    foreign.mkdir()
    (foreign / "precious.py").write_text("mine", encoding="utf-8")

    class _GapFillOnlyLLM:
        def call(self, prompt, response_model=None, **kw):
            if response_model is GapFills:
                return GapFills()
            raise AssertionError(f"unexpected LLM call with response_model={response_model} — translate should not re-run")

    first = run_pipeline(spec=ROUTER_SPEC, llm=_GapFillOnlyLLM(), out_root=str(tmp_path))
    assert any(e.node == "write" and e.kind == "WriterError" for e in first["errors"])

    # "Resume" past the checkpoint: same already-parsed spec, a different output
    # root (as a user re-running `w2a convert --out` elsewhere would do) — no
    # translate() call happens, proven by _GapFillOnlyLLM raising on anything else.
    second_out = tmp_path / "retry"
    second = run_pipeline(spec=ROUTER_SPEC, llm=_GapFillOnlyLLM(), out_root=str(second_out))
    assert second["errors"] == []
    assert (second["write_result"].project_dir / "crew.py").exists()
