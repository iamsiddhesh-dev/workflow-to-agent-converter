"""Spec Forge — the WorkflowSpec IR, its linter, translator, and ambiguity scoring."""

from w2a.spec.ambiguity import AmbiguityReport, score
from w2a.spec.lint import LintIssue, is_clean, lint
from w2a.spec.model import (
    AgentSpec,
    Flow,
    TaskSpec,
    ToolSpec,
    Workflow,
    WorkflowSpec,
    human_summary,
)
from w2a.spec.translate import build_prompt, translate

__all__ = [
    "AgentSpec",
    "AmbiguityReport",
    "Flow",
    "LintIssue",
    "TaskSpec",
    "ToolSpec",
    "Workflow",
    "WorkflowSpec",
    "build_prompt",
    "human_summary",
    "is_clean",
    "lint",
    "score",
    "translate",
]
