# Pattern Vault

Five control-flow shapes cover every `flow.pattern` a WorkflowSpec can declare
(see `spec/model.py::Flow`). Generation (Phase 4) never invents structure — it
picks one of these five and fills in the spec's own agents, tasks, and tools.
Each pattern is a directory under `src/w2a/templates/<pattern>/` holding the
same seven files: `crew.py.j2`, `tools.py.j2`, `main.py.j2`, `config.py.j2`,
`requirements.txt.j2`, `.env.example.j2`, `README.md.j2`.

## The five patterns

| Pattern | Shape | CrewAI realization |
|---|---|---|
| `sequential` | A -> B -> C pipeline, every step feeds the next | `Process.sequential`; each `Task.context` is exactly its `depends_on` |
| `router` | Classify once, branch to specialist agents | Classifier task runs first; every downstream task is a `ConditionalTask` whose condition matches the classifier's output against that task's own domain keywords |
| `report` | Gather from N sources, analyze, format one artifact | Independent gather tasks (no `depends_on`) feed a single final task with `context=[all gather tasks]` and `output_file` set |
| `approval` | Draft -> human checkpoint -> finalize | The task with `human_checkpoint=True` in the spec gets `human_input=True` on the CrewAI `Task`, pausing for terminal input before the next task runs |
| `watcher` | Poll -> detect -> notify, repeatedly | `crew.py` exposes `run_once()`; `main.py` calls it in a loop with a sleep interval (`--once` flag runs a single pass for testing) |

Structural differences live in `crew.py.j2` and `main.py.j2` per pattern;
`tools.py.j2`, `config.py.j2`, `requirements.txt.j2`, `.env.example.j2`, and
`README.md.j2` are thin per-pattern files that `{% include %}` shared partials
in `templates/_common/`, so the LLM-wiring, tool-stub shape, and boilerplate
never drift between patterns.

## Benchmark -> pattern mapping

This mapping is the generalization thesis in miniature: two categories (ops,
dev/eng), six workflows, and every one of them reduces to one of the five
shapes without forcing a fit.

| Benchmark (`examples/workflows/`) | Category | Pattern | Why |
|---|---|---|---|
| `onboarding.md` | ops | `sequential` | Provision -> welcome email -> checklist -> verify, each step strictly after the last, no branching or approval. |
| `ticket_triage.md` | ops | `router` | Classify each ticket (bug/billing/question), branch: urgent pings on-call, everything else gets labeled and queued. |
| `weekly_report.md` | ops | `report` | Gather from three independent sources (eng tickets, support queue, finance note) and format one Friday status doc; explicitly not auto-sent, so it stops at the artifact. |
| `review_routing.md` | dev | `router` | Classify changed files by owning area (frontend/infra/etc.), branch to the owning reviewer; sensitive areas add a second-reviewer branch. |
| `pr_summary.md` | dev | `report` | Analyze one diff, format one PR-comment artifact; trivial diffs take a shorter branch of the same report task, not a different pattern. |
| `bug_triage.md` | dev | `router` + `approval` | Classify severity/area and file it (router), but a suspected P0 needs a human to confirm before anything pages on-call (approval gate on that one branch). |

`bug_triage` is the one workflow that genuinely needs two patterns composed —
documented here rather than forced into a sixth pattern, per PLAN.md's
"cut pattern count before cutting the deterministic/LLM boundary" rule. The
selector (`selector.py`) picks the spec's primary `flow.pattern` (`router`);
the human checkpoint on the P0 branch is expressed at the task level
(`human_checkpoint=True`), which the `router` templates honor by adding
`human_input=True` to that one task — no separate `router_approval` pattern
directory is needed.

## Built-in tools

`builtin_tools.py` (real, not stubbed): `read_file`, `write_file`, `http_get`,
`parse_csv`, `write_markdown_report`, `send_message` (writes to an `outbox/`
folder as a zero-cost stand-in for Slack/email). Every generated tool that
resolves to one of these gets the real implementation; anything else becomes
an explicit `MOCK_MODE` stub with a `# TODO: connect real <system>` marker —
the registry that enforces this (no invented imports) lands in Phase 4.
