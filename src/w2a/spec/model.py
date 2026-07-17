"""WorkflowSpec — the structured intermediate representation.

This is the load-bearing contract of the whole converter: the translator (Phase 2)
produces it, the linter checks it, and every later phase (pattern selection,
generation, validation) reads it. The field descriptions here are not decoration —
they are rendered into the translation prompt (see ``translate.py``), so an LLM
filling this schema sees exactly these words. Keep them precise and grounded in
what the *user's description* can actually support.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Pattern = Literal["sequential", "router", "report", "approval", "watcher"]


class Workflow(BaseModel):
    """Top-level identity of the business process being automated."""

    name: str = Field(description="Short human title for the workflow, e.g. 'Support Ticket Triage'.")
    description: str = Field(
        description="One or two sentences restating the process in plain language, grounded in the user's own words."
    )
    trigger: str = Field(
        description="What starts the workflow: an event ('a ticket arrives'), a schedule ('every Friday'), or a manual run."
    )
    category: Literal["ops", "dev"] = Field(
        description="'ops' for internal business operations, 'dev' for software/engineering-team workflows."
    )


class AgentSpec(BaseModel):
    """One role in the crew. Prefer 2-4 well-defined agents over one agent per sentence."""

    id: str = Field(description="Stable snake_case identifier, unique within the spec; tasks and edges reference it.")
    role: str = Field(description="Short job title, e.g. 'Ticket Classifier'. This becomes the CrewAI Agent role.")
    goal: str = Field(description="The single outcome this agent is responsible for, phrased as an objective.")
    backstory_hint: str = Field(
        description="A one-line persona seed (expertise, temperament) the generator expands into a CrewAI backstory. Not the full backstory."
    )


class TaskSpec(BaseModel):
    """A unit of work owned by exactly one agent, with explicit dependencies."""

    id: str = Field(description="Stable snake_case identifier, unique within the spec; depends_on and edges reference it.")
    description: str = Field(description="What this task does, grounded in the user's description — the CrewAI Task description.")
    agent_id: str = Field(description="The id of the AgentSpec that performs this task. Must match an existing agent.")
    depends_on: list[str] = Field(
        default_factory=list,
        description="ids of tasks that must complete before this one; their outputs become this task's context.",
    )
    expected_output: str = Field(
        description="Concrete description of the artifact or result this task produces. Never leave empty — it drives generation and validation."
    )
    human_checkpoint: bool = Field(
        default=False,
        description="True if the user's description implies a person must review or approve before the workflow continues.",
    )


class ToolSpec(BaseModel):
    """A capability a task needs. Grounded in the text — do not invent tools no step requires."""

    id: str = Field(description="Stable snake_case identifier, unique within the spec.")
    name: str = Field(description="Human name of the capability, e.g. 'Slack notifier' or 'CSV parser'.")
    purpose: str = Field(description="Why the workflow needs this tool, grounded in the user's description.")
    category: Literal["builtin", "external"] = Field(
        description="'builtin' if it maps to a generic capability (file I/O, HTTP GET, CSV, report writer, message send); 'external' if it needs a specific third-party system (Slack, Jira, GitHub)."
    )
    inputs: str = Field(description="What the tool takes in, in plain language, e.g. 'ticket text'.")
    outputs: str = Field(description="What the tool returns, in plain language, e.g. 'category label and priority'.")


class Flow(BaseModel):
    """The overall control-flow shape and the task-to-task edges realizing it."""

    pattern: Pattern = Field(
        description=(
            "The dominant shape of the workflow: "
            "'sequential' (A->B->C pipeline), "
            "'router' (classify then branch to specialists), "
            "'report' (gather -> analyze -> format an artifact), "
            "'approval' (draft -> human checkpoint -> finalize), "
            "'watcher' (poll -> detect -> notify)."
        )
    )
    edges: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Directed (from_task_id, to_task_id) pairs; every id must be an existing task. Encodes execution order/branching.",
    )


class WorkflowSpec(BaseModel):
    """The complete structured design translated from a plain-language workflow description."""

    workflow: Workflow = Field(description="Top-level identity of the process.")
    agents: list[AgentSpec] = Field(description="The crew roles, 2-4 preferred.")
    tasks: list[TaskSpec] = Field(description="The units of work, each owned by an agent.")
    tools: list[ToolSpec] = Field(description="Capabilities the tasks need; may be empty.")
    flow: Flow = Field(description="Control-flow pattern and task edges.")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Defaults chosen where any reasonable choice works, so the user can see what was assumed rather than silently invented.",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Open questions where the answer would change the design — surfaced to the user instead of guessed. Each entry is a question.",
    )


def human_summary(spec: WorkflowSpec) -> str:
    """Render a spec as a readable summary for eyeballing against the original description."""
    lines: list[str] = []
    w = spec.workflow
    lines.append(f"# {w.name}  [{w.category}]")
    lines.append(f"{w.description}")
    lines.append(f"Trigger: {w.trigger}")
    lines.append(f"Pattern: {spec.flow.pattern}")

    lines.append("\nAgents:")
    for a in spec.agents:
        lines.append(f"  - {a.id} ({a.role}): {a.goal}")

    lines.append("\nTasks:")
    for t in spec.tasks:
        dep = f" <- {', '.join(t.depends_on)}" if t.depends_on else ""
        check = " [human checkpoint]" if t.human_checkpoint else ""
        lines.append(f"  - {t.id} @{t.agent_id}{dep}{check}: {t.description}")
        lines.append(f"      -> {t.expected_output}")

    if spec.tools:
        lines.append("\nTools:")
        for tool in spec.tools:
            lines.append(f"  - {tool.name} [{tool.category}]: {tool.purpose}")

    if spec.assumptions:
        lines.append("\nAssumptions:")
        lines.extend(f"  - {a}" for a in spec.assumptions)

    if spec.ambiguities:
        lines.append("\nOpen questions:")
        lines.extend(f"  - {q}" for q in spec.ambiguities)

    return "\n".join(lines)
