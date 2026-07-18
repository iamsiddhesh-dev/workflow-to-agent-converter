"""The converter pipeline as a LangGraph graph.

``parse -> lint -> select_pattern -> render -> gap_fill -> write``. After each
of the first four nodes a conditional edge routes to END if the state has
accumulated errors — so a lint-failing spec lands as a structured pipeline
error, never a traceback. ``gap_fill`` is deliberately non-fatal: if the LLM
is down the skeleton ships as-is with a warning, because a runnable project
with hint-level prose beats no project.
"""

from __future__ import annotations

import logging
from typing import Callable

from langgraph.graph import END, StateGraph

from w2a.generate.gapfill import gap_fill
from w2a.generate.manifest import build_manifest
from w2a.generate.registry import resolve_all
from w2a.generate.writer import write_project
from w2a.llm import LLM, LLMError
from w2a.pipeline.state import PipelineError, PipelineState
from w2a.spec.lint import lint
from w2a.spec.translate import translate
from w2a.templates.render import render_pattern
from w2a.templates.selector import SelectionResult, select_pattern, structural_confidence

logger = logging.getLogger(__name__)

_NODE_ORDER = ["parse", "lint", "select_pattern", "render", "gap_fill", "write"]


def _guard(name: str, fn: Callable[[PipelineState], dict]) -> Callable[[PipelineState], dict]:
    def node(state: PipelineState) -> dict:
        try:
            return fn(state)
        except Exception as exc:  # noqa: BLE001 - the whole point: errors become state
            logger.exception("pipeline node %s failed", name)
            return {"errors": [PipelineError(node=name, kind=type(exc).__name__, message=str(exc))]}

    return node


def build_graph(llm: LLM | None = None):
    """Compile the pipeline graph. ``llm`` is shared by every LLM-touching node."""

    def parse(state: PipelineState) -> dict:
        if state.get("spec") is not None:
            return {}
        source = state.get("source", "").strip()
        if not source:
            return {"errors": [PipelineError(node="parse", kind="EmptyInput", message="no workflow description provided")]}
        return {"spec": translate(source, llm=llm), "translated": True}

    def lint_node(state: PipelineState) -> dict:
        issues = lint(state["spec"])
        update: dict = {"lint_issues": issues}
        error_issues = [i for i in issues if i.severity == "error"]
        if error_issues:
            update["errors"] = [
                PipelineError(node="lint", kind="LintError", message=str(i)) for i in error_issues
            ]
        if any(i.severity == "warning" for i in issues):
            update["warnings"] = [f"lint: {i}" for i in issues if i.severity == "warning"]
        return update

    def select_node(state: PipelineState) -> dict:
        spec = state["spec"]
        try:
            return {"selection": select_pattern(spec, llm=llm)}
        except LLMError as exc:
            fallback = SelectionResult(
                pattern=spec.flow.pattern,
                confidence=structural_confidence(spec),
                source="deterministic",
                reasoning="LLM fallback unavailable — kept the declared pattern despite low structural confidence",
            )
            return {
                "selection": fallback,
                "warnings": [f"select_pattern: LLM fallback failed ({exc}); kept declared pattern {spec.flow.pattern!r}"],
            }

    def render_node(state: PipelineState) -> dict:
        spec = state["spec"]
        resolutions = resolve_all(spec)
        skeleton = render_pattern(spec, state["selection"].pattern, resolutions)
        empty = [name for name, content in skeleton.items() if not content.strip()]
        if empty:
            raise ValueError(f"template render produced empty content for: {empty}")
        return {"resolutions": resolutions, "skeleton": skeleton, "files": skeleton}

    def gap_fill_node(state: PipelineState) -> dict:
        files, report = gap_fill(
            state["spec"], state["selection"].pattern, state["resolutions"], llm=llm
        )
        update: dict = {"files": files, "gapfill_report": report}
        if report.error:
            update["warnings"] = [f"gap_fill: skipped, skeleton shipped as-is ({report.error})"]
        elif report.rejected:
            update["warnings"] = [f"gap_fill: rejected {len(report.rejected)} fill(s), see manifest"]
        return update

    def write_node(state: PipelineState) -> dict:
        llm_calls = []
        if state.get("translated"):
            llm_calls.append("translate")
        if state["selection"].source == "llm_fallback":
            llm_calls.append("select_pattern_fallback")
        if state.get("gapfill_report") is not None and state["gapfill_report"].llm_called:
            llm_calls.append("gap_fill")
        manifest = build_manifest(
            spec=state["spec"],
            selection=state["selection"],
            resolutions=state["resolutions"],
            files=state["files"],
            gapfill_report=state.get("gapfill_report"),
            source_description=state.get("source") or None,
            llm_calls=llm_calls,
        )
        result = write_project(state["files"], manifest, out_root=state.get("out_root", "generated"))
        return {"manifest": manifest, "write_result": result}

    implementations = {
        "parse": parse,
        "lint": lint_node,
        "select_pattern": select_node,
        "render": render_node,
        "gap_fill": gap_fill_node,
        "write": write_node,
    }

    graph = StateGraph(PipelineState)
    for name in _NODE_ORDER:
        graph.add_node(name, _guard(name, implementations[name]))
    graph.set_entry_point("parse")
    for name, successor in zip(_NODE_ORDER, _NODE_ORDER[1:]):
        graph.add_conditional_edges(
            name,
            lambda state, nxt=successor: END if state.get("errors") else nxt,
            [successor, END],
        )
    graph.add_edge("write", END)
    return graph.compile()


def run_pipeline(
    source: str | None = None,
    spec=None,
    llm: LLM | None = None,
    out_root: str = "generated",
    pre_translated: bool = False,
) -> PipelineState:
    """Run the full pipeline from a description (or a pre-translated spec).

    Pass ``pre_translated=True`` when the injected ``spec`` came from an LLM
    translation done upstream (e.g. the CLI's clarify loop), so the manifest's
    llm_calls provenance stays honest.
    """
    initial: PipelineState = {
        "source": source or "",
        "out_root": out_root,
        "translated": pre_translated,
        "errors": [],
        "warnings": [],
    }
    if spec is not None:
        initial["spec"] = spec
    return build_graph(llm).invoke(initial)
