"""Pure-Python WorkflowSpec linter — no LLM.

Catches the structural defects an LLM translator produces even when its JSON is
schema-valid: references to agents/tasks that don't exist, dependency cycles,
tasks disconnected from the flow, tools nothing uses, empty expected outputs.
Structural integrity is cheap and deterministic to check here, which keeps it out
of the (expensive, non-deterministic) LLM path.
"""

from __future__ import annotations

from dataclasses import dataclass

from w2a.spec.model import WorkflowSpec
from w2a.spec.textutils import content_words_set as _content_words


@dataclass(frozen=True)
class LintIssue:
    code: str
    message: str
    severity: str = "error"  # "error" blocks generation; "warning" is advisory

    def __str__(self) -> str:
        return f"[{self.severity}:{self.code}] {self.message}"


def _duplicates(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for i in ids:
        if i in seen and i not in dups:
            dups.append(i)
        seen.add(i)
    return dups


def _find_cycle(nodes: list[str], deps: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle as an ordered id list, or None. deps[t] = tasks t depends on."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = GREY
        stack.append(n)
        for m in deps.get(n, []):
            if m not in color:
                continue  # dangling dep, reported elsewhere
            if color[m] == GREY:
                return stack[stack.index(m):] + [m]
            if color[m] == WHITE:
                found = visit(m)
                if found is not None:
                    return found
        stack.pop()
        color[n] = BLACK
        return None

    for n in nodes:
        if color[n] == WHITE:
            found = visit(n)
            if found is not None:
                return found
    return None


def lint(spec: WorkflowSpec) -> list[LintIssue]:
    """Return every structural issue found in the spec, empty list if clean."""
    issues: list[LintIssue] = []

    agent_ids = [a.id for a in spec.agents]
    task_ids = [t.id for t in spec.tasks]
    agent_id_set = set(agent_ids)
    task_id_set = set(task_ids)

    if not spec.agents:
        issues.append(LintIssue("no_agents", "Spec has no agents."))
    if not spec.tasks:
        issues.append(LintIssue("no_tasks", "Spec has no tasks."))

    for dup in _duplicates(agent_ids):
        issues.append(LintIssue("duplicate_agent_id", f"Agent id '{dup}' is defined more than once."))
    for dup in _duplicates(task_ids):
        issues.append(LintIssue("duplicate_task_id", f"Task id '{dup}' is defined more than once."))
    for dup in _duplicates([t.id for t in spec.tools]):
        issues.append(LintIssue("duplicate_tool_id", f"Tool id '{dup}' is defined more than once."))

    # Task-level reference and content checks.
    for t in spec.tasks:
        if t.agent_id not in agent_id_set:
            issues.append(
                LintIssue("dangling_agent_id", f"Task '{t.id}' is owned by unknown agent '{t.agent_id}'.")
            )
        for dep in t.depends_on:
            if dep not in task_id_set:
                issues.append(
                    LintIssue("dangling_dependency", f"Task '{t.id}' depends on unknown task '{dep}'.")
                )
            elif dep == t.id:
                issues.append(LintIssue("self_dependency", f"Task '{t.id}' depends on itself."))
        if not t.expected_output.strip():
            issues.append(LintIssue("empty_expected_output", f"Task '{t.id}' has an empty expected_output."))

    # Flow edge references.
    for src, dst in spec.flow.edges:
        if src not in task_id_set:
            issues.append(LintIssue("dangling_edge", f"Flow edge source '{src}' is not a task."))
        if dst not in task_id_set:
            issues.append(LintIssue("dangling_edge", f"Flow edge target '{dst}' is not a task."))

    # Orphan tasks: with 2+ tasks, a task connected to nothing is almost always a translation slip.
    if len(spec.tasks) > 1:
        depended_upon = {dep for t in spec.tasks for dep in t.depends_on}
        edge_touched = {e for edge in spec.flow.edges for e in edge}
        for t in spec.tasks:
            connected = bool(t.depends_on) or t.id in depended_upon or t.id in edge_touched
            if not connected:
                issues.append(
                    LintIssue("orphan_task", f"Task '{t.id}' is disconnected from the flow (no deps, edges, or dependents).", "warning")
                )

    # Unused tools: the schema has no task->tool link, so "used" is approximated by
    # word overlap between a tool's name/purpose and the task text. A tool sharing no
    # content word with any task is almost always a tool the workflow doesn't need.
    haystack = _content_words(
        " ".join(t.description + " " + t.expected_output for t in spec.tasks)
    )
    for tool in spec.tools:
        tool_words = _content_words(tool.name + " " + tool.purpose)
        if tool_words and tool_words.isdisjoint(haystack):
            issues.append(
                LintIssue("unused_tool", f"Tool '{tool.name}' ({tool.id}) is referenced by no task.", "warning")
            )

    # Cyclic dependencies via DFS over the depends_on graph.
    deps = {t.id: [d for d in t.depends_on if d in task_id_set] for t in spec.tasks}
    cycle = _find_cycle(task_ids, deps)
    if cycle is not None:
        issues.append(LintIssue("cyclic_dependency", f"Dependency cycle: {' -> '.join(cycle)}."))

    return issues


def is_clean(spec: WorkflowSpec, warnings_ok: bool = True) -> bool:
    """True if the spec has no blocking issues (warnings ignored unless warnings_ok=False)."""
    issues = lint(spec)
    if warnings_ok:
        return not any(i.severity == "error" for i in issues)
    return not issues
