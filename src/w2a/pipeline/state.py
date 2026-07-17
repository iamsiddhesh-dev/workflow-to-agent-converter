"""Typed state for the converter pipeline.

Errors are data, not exceptions: every node catches its own failures and
appends a ``PipelineError`` instead of raising past the graph, so a failed run
ends with a state that says exactly which node broke and why. ``errors`` and
``warnings`` use the additive reducer — nodes only ever append.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel

from w2a.generate.gapfill import GapFillReport
from w2a.generate.writer import WriteResult
from w2a.spec.lint import LintIssue
from w2a.spec.model import WorkflowSpec
from w2a.templates.selector import SelectionResult


class PipelineError(BaseModel):
    node: str
    kind: str
    message: str

    def __str__(self) -> str:
        return f"[{self.node}] {self.kind}: {self.message}"


class PipelineState(TypedDict, total=False):
    source: str
    out_root: str
    spec: WorkflowSpec
    translated: bool
    lint_issues: list[LintIssue]
    selection: SelectionResult
    resolutions: dict
    skeleton: dict[str, str]
    files: dict[str, str]
    gapfill_report: GapFillReport
    manifest: dict
    write_result: WriteResult
    errors: Annotated[list[PipelineError], operator.add]
    warnings: Annotated[list[str], operator.add]
