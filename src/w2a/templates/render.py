"""WorkflowSpec + pattern -> generated project file contents.

Structure is deterministic template output — no LLM involved here (Phase 4
owns the bounded LLM gap-fill). This module only builds the render context and
runs the Jinja2 environment; the pattern-specific wiring (sequential ordering,
router conditions, report aggregation, approval checkpoints, watcher polling)
lives in each pattern directory's ``crew.py.j2`` / ``main.py.j2``.

All spec text (task descriptions, agent goals, tool purposes...) is rendered
through the ``pyrepr`` filter (Python's ``repr``), so arbitrary user-derived
text — quotes, backslashes, newlines — always produces a valid Python string
literal. Never interpolate spec text into a template as raw source.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from w2a.spec.model import Pattern, TaskSpec, WorkflowSpec
from w2a.spec.textutils import content_words_ordered as _content_words

TEMPLATES_ROOT = Path(__file__).parent

OUTPUT_FILES = [
    "crew.py",
    "tools.py",
    "main.py",
    "config.py",
    "requirements.txt",
    ".env.example",
    "README.md",
]

_TEMPLATE_NAMES = {
    "requirements.txt": "requirements.txt.j2",
    ".env.example": ".env.example.j2",
}

PATTERN_DIRS: dict[Pattern, str] = {
    "sequential": "sequential_pipeline",
    "router": "triage_router",
    "report": "report_generator",
    "approval": "approval_gate",
    "watcher": "scheduled_watcher",
}

PATTERN_NOTES: dict[Pattern, str] = {
    "sequential": "Tasks run strictly in dependency order, each feeding its output as context to the next.",
    "router": "The root task(s) classify the input; every downstream task is a CrewAI ConditionalTask that only runs when its own keywords match the classifier's output.",
    "report": "Independent gather tasks run first; the final task(s) — nothing depends on them — aggregate that context and write the report artifact to output/.",
    "approval": "Any task marked as a human checkpoint in the spec pauses for terminal approval (CrewAI human_input=True) before the crew continues.",
    "watcher": "main.py polls in a loop, calling crew.run_once() every --interval seconds; pass --once to run a single pass (used for validation).",
}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "workflow"


def _topo_order(tasks: list[TaskSpec]) -> list[TaskSpec]:
    """Order tasks so every dependency is emitted before its dependents.

    Assumes a lint-clean spec (no dangling deps, no cycles) — this is a
    rendering concern, not a validation one, so it raises rather than trying
    to route around a cycle the linter should already have caught.
    """
    by_id = {t.id: t for t in tasks}
    remaining = {t.id: set(d for d in t.depends_on if d in by_id) for t in tasks}
    ordered: list[TaskSpec] = []
    placed: set[str] = set()
    while remaining:
        ready = [tid for tid, deps in remaining.items() if deps <= placed]
        if not ready:
            raise ValueError(f"Cannot topologically order tasks (cycle among {list(remaining)}).")
        for tid in sorted(ready):
            ordered.append(by_id[tid])
            placed.add(tid)
            del remaining[tid]
    return ordered


def build_context(spec: WorkflowSpec, pattern: Pattern, resolutions: dict | None = None) -> dict:
    """Build the Jinja2 render context for one spec+pattern combination.

    ``resolutions`` maps tool id -> registry resolution (``w2a.generate.registry``);
    duck-typed here (a resolved builtin has ``.source``) so templates stay a layer
    below the generator. ``None`` keeps the Phase-3 behavior: every tool is a stub.
    """
    agent_var = {a.id: f"agent_{a.id}" for a in spec.agents}
    ordered_tasks = _topo_order(list(spec.tasks))
    depended_upon = {dep for t in spec.tasks for dep in t.depends_on}
    edge_touched = {e for edge in spec.flow.edges for e in edge}

    agents = [
        {
            "id": a.id,
            "var": agent_var[a.id],
            "role": a.role,
            "goal": a.goal,
            "backstory": a.backstory_hint,
        }
        for a in spec.agents
    ]

    tasks = [
        {
            "id": t.id,
            "var": f"task_{t.id}",
            "agent_id": t.agent_id,
            "agent_var": agent_var[t.agent_id],
            "description": t.description,
            "expected_output": t.expected_output,
            "depends_on": list(t.depends_on),
            "depends_on_vars": [f"task_{d}" for d in t.depends_on],
            "human_checkpoint": t.human_checkpoint,
            "is_root": not t.depends_on,
            "is_leaf": t.id not in depended_upon,
            "is_periodic": not (bool(t.depends_on) or t.id in depended_upon or t.id in edge_touched),
            "condition_keywords": _content_words(t.description + " " + t.expected_output)[:6],
        }
        for t in ordered_tasks
    ]

    tools: list[dict] = []
    tool_imports: list[str] = []
    tool_objects: list[str] = []
    emitted_builtins: set[str] = set()
    has_stub_tools = False
    for tool in spec.tools:
        res = (resolutions or {}).get(tool.id)
        source = getattr(res, "source", None)
        entry = {
            "id": tool.id,
            "func": tool.id,
            "name": tool.name,
            "purpose": tool.purpose,
            "category": tool.category,
            "inputs": tool.inputs,
            "outputs": tool.outputs,
            "resolved": source is not None,
            "builtin_name": None,
            "source": None,
            "emit_source": False,
        }
        if source is not None:
            entry["builtin_name"] = res.name
            entry["source"] = source
            entry["emit_source"] = res.name not in emitted_builtins
            emitted_builtins.add(res.name)
            if res.name not in tool_objects:
                tool_objects.append(res.name)
            for imp in res.imports:
                if imp not in tool_imports:
                    tool_imports.append(imp)
        else:
            has_stub_tools = True
            tool_objects.append(tool.id)
        tools.append(entry)

    return {
        "workflow": {
            "name": spec.workflow.name,
            "description": spec.workflow.description,
            "trigger": spec.workflow.trigger,
            "category": spec.workflow.category,
            "slug": _slugify(spec.workflow.name),
        },
        "pattern": pattern,
        "pattern_notes": PATTERN_NOTES[pattern],
        "agents": agents,
        "tasks": tasks,
        "has_human_checkpoint": any(t.human_checkpoint for t in spec.tasks),
        "tools": tools,
        "tool_imports": sorted(tool_imports),
        "tool_objects": tool_objects,
        "has_stub_tools": has_stub_tools,
        "assumptions": list(spec.assumptions),
    }


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_ROOT)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pyrepr"] = repr
    return env


def render_files(context: dict, pattern: Pattern) -> dict[str, str]:
    """Render one pattern's template set from a prebuilt context (gap-fill re-renders with this)."""
    env = _environment()
    pattern_dir = PATTERN_DIRS[pattern]
    rendered: dict[str, str] = {}
    for filename in OUTPUT_FILES:
        template_name = _TEMPLATE_NAMES.get(filename, f"{filename}.j2")
        template = env.get_template(f"{pattern_dir}/{template_name}")
        rendered[filename] = template.render(**context)
    return rendered


def render_pattern(spec: WorkflowSpec, pattern: Pattern, resolutions: dict | None = None) -> dict[str, str]:
    """Render one pattern's template set against a spec. Returns {output filename: content}."""
    return render_files(build_context(spec, pattern, resolutions), pattern)
