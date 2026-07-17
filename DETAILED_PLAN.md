# Workflow-to-Agent Converter — Detailed Execution Plan

> **Companion to [PLAN.md](PLAN.md).** PLAN.md holds the architecture rationale (LangGraph converter → CrewAI output, zero-cost stack, the five flagged failure modes and their owning phases). This file adds task-level breakdowns with a **model per task**, exact file paths, the WorkflowSpec schema spelled out, the template file set per pattern, drafted translation/gap-fill/repair prompts, the CLI spec, and acceptance checks. One phase per session; switch models per task to save tokens.

**Model shorthand:** `H` = Haiku 4.5 · `S` = Sonnet 5 · `O` = Opus 4.8 · `F` = Fable 5
**Runtime LLM (translator + generated crews):** Gemini 2.5 Flash free tier (primary) / Groq free tier (fallback), per PLAN.md.

---

## Repo file tree (end state)

```
w2a/
├── pyproject.toml              # langgraph, crewai, crewai-tools, pydantic, jinja2, ruff, typer
├── README.md                   # hook, architecture diagram, two-framework rationale, limitations
├── DECISIONS.md                # framework split, provider choice, the four load-bearing ideas
├── RESULTS.md                  # Phase 6 generalization matrix + Phase 7 before/after
├── .env.example                # GEMINI_API_KEY, GROQ_API_KEY
├── examples/workflows/         # the 6 benchmark descriptions (messy founder-speak paragraphs)
│   ├── onboarding.md  ticket_triage.md  weekly_report.md          (ops)
│   └── review_routing.md  pr_summary.md  bug_triage.md            (dev/eng)
├── generated/                  # output projects (.gitignore'd except two committed samples)
├── src/w2a/
│   ├── cli.py                  # typer app: convert / validate / demo
│   ├── llm.py                  # provider wrapper: single call-site, retries, timeout, token log
│   ├── spec/
│   │   ├── model.py            # WorkflowSpec Pydantic models
│   │   ├── translate.py        # NL → spec (structured output + re-ask envelope)
│   │   ├── lint.py             # pure-Python spec linter
│   │   └── ambiguity.py        # score + clarify-mode threshold
│   ├── templates/
│   │   ├── registry.py         # closed tool registry + fuzzy matcher (Phase 7)
│   │   ├── builtin_tools.py    # real implementations: file I/O, HTTP GET, CSV, report, outbox
│   │   ├── selector.py         # pattern selector (deterministic + LLM fallback)
│   │   └── <pattern>/          # one dir per pattern (5): sequential_pipeline, triage_router,
│   │       │                   #   report_generator, approval_gate, scheduled_watcher
│   │       ├── crew.py.j2  tools.py.j2  main.py.j2  config.py.j2
│   │       ├── requirements.txt.j2  .env.example.j2  README.md.j2
│   ├── generate/
│   │   ├── gapfill.py          # bounded LLM gap-filling + AST re-verification
│   │   ├── writer.py           # idempotent project writer + manifest.json
│   │   └── manifest.py
│   ├── validate/
│   │   ├── static_tier.py      # py_compile, ruff, AST import allowlist
│   │   ├── env_tier.py         # fresh venv + install + import
│   │   ├── exec_tier.py        # MOCK_MODE dry run + artifact assertions
│   │   ├── specificity.py      # domain-noun coverage score
│   │   └── repair.py           # bounded repair loop
│   └── pipeline/
│       ├── state.py            # typed LangGraph pipeline state
│       └── graph.py            # parse→lint→select→render→gap_fill→write→validate→repair
└── tests/
    ├── fixtures/specs/          # frozen Phase-6 spec regression fixtures
    ├── test_lint.py  test_translate.py  test_templates.py
    ├── test_generate.py  test_validate.py  test_adversarial.py
```

---

## Phase 1 — Blueprint

### 1.1 Scaffold + deps — **Model: H**
- Repo tree above; `pyproject.toml` pinned; `generated/` in `.gitignore`; logging config; `.env.example`.
- **Accept:** `pip install -e .` clean; `w2a --help` shows the three stub commands.

### 1.2 Provider wrapper — **Model: S**
- `llm.py`: one `call(prompt, response_model: type[BaseModel] | None, **kw)` entry — Gemini primary, Groq fallback on 429/5xx; retries (2, backoff), 60 s timeout, token counts logged JSON-lines. Used by the converter **and** injected into generated crews (via their `config.py`).
- **Accept:** smoke test 1 — structured-output call parses into a toy Pydantic model.

### 1.3 CrewAI hello-world — **Model: S**
- Hand-write a 2-agent crew running on the free-tier LLM — proves the *generation target* executes on the zero-cost stack before anything generates it.
- **Accept:** smoke test 2 passes; kept under `examples/hello_crew/` for reference.

### 1.4 Benchmark descriptions + DECISIONS.md — **Model: S** (descriptions) / **H** (DECISIONS)
- Write the 6 workflow paragraphs the way a founder talks (messy, underspecified — e.g. ticket_triage.md: *"When support tickets come in someone needs to figure out if it's a bug or billing thing, urgent ones should ping whoever's on call, and we want a summary at the end of the week of what came in."*). DECISIONS.md records the framework split + provider choice.
- **Accept (phase DoD):** both smoke tests pass on free tier; 6 descriptions committed; DECISIONS.md written.

---

## Phase 2 — Spec Forge

### 2.1 WorkflowSpec schema — **Model: O**
- `spec/model.py`, exactly per PLAN.md:
  ```python
  class Workflow(BaseModel):  name: str; description: str; trigger: str; category: Literal["ops","dev"]
  class AgentSpec(BaseModel): id: str; role: str; goal: str; backstory_hint: str
  class TaskSpec(BaseModel):  id: str; description: str; agent_id: str; depends_on: list[str] = []
                              expected_output: str; human_checkpoint: bool = False
  class ToolSpec(BaseModel):  id: str; name: str; purpose: str
                              category: Literal["builtin","external"]; inputs: str; outputs: str
  class Flow(BaseModel):      pattern: Literal["sequential","router","report","approval","watcher"]
                              edges: list[tuple[str, str]]
  class WorkflowSpec(BaseModel):
      workflow: Workflow; agents: list[AgentSpec]; tasks: list[TaskSpec]
      tools: list[ToolSpec]; flow: Flow
      assumptions: list[str] = []; ambiguities: list[str] = []
  ```
- **Accept:** schema round-trips JSON; docstrings on every field (they feed the translation prompt).

### 2.2 Spec linter — **Model: S**
- `spec/lint.py` pure-Python checks: dangling `agent_id`/`depends_on`, unused tools, orphan tasks, cyclic deps (DFS), empty `expected_output`. Returns a typed issue list.
- **Accept:** unit test seeds each defect; linter catches every one.

### 2.3 Translation prompt — **Model: O**
- `spec/translate.py` prompt draft:
  ```
  Convert this plain-language business process into a WorkflowSpec JSON
  (schema below). Rules:
  - Do NOT invent specifics the text doesn't support. Unknowns go to:
    * ambiguities[] — a question for the user, when the answer changes the design
    * assumptions[] — the default you chose, when any reasonable default works
  - Every task needs an owning agent; every tool needs a purpose grounded in the text.
  - Prefer fewer, well-defined agents (2–4) over one agent per sentence.
  - flow.pattern: sequential | router | report | approval | watcher — pick the
    dominant shape; put a human_checkpoint on any step the text implies approval for.
  Output ONLY the JSON.
  ```
  Two few-shot worked examples: one ops (onboarding), one dev/eng (PR summary).
- Malformed-output envelope: Pydantic failure → re-ask with the validation error appended, max 2 retries → hard fail, raw output saved to `generated/_debug/`.
- **Accept:** all 6 benchmarks translate to lint-clean specs; fault-injecting mock LLM exercises the re-ask path.

### 2.4 Ambiguity scoring + clarify mode — **Model: O**
- `spec/ambiguity.py`: score = Σ(severity per ambiguity: design-changing = 3, tool-choice = 2, naming = 1); threshold (default ≥4) flips CLI into ask-clarifying-questions mode (print questions, accept answers, re-translate with answers appended) vs proceed-with-assumptions (assumptions echoed to the user).
- **Accept (phase DoD):** "just handle my support stuff" produces populated `ambiguities[]` + clarify mode, never a confabulated spec; round-trip summaries of all 6 specs eyeballed against originals.

---

## Phase 3 — Pattern Vault

### 3.1 Pattern taxonomy + benchmark mapping — **Model: S**
- Document in `templates/README.md`: the 5 patterns and the expected mapping (onboarding→sequential, ticket triage→router, weekly report→report, review routing→router, PR summary→report, bug triage→router+approval). This mapping is the generalization thesis in miniature.
- **Accept:** mapping table committed.

### 3.2 Jinja2 template sets — **Model: S**
- Per pattern directory, 7 files (`crew.py.j2, tools.py.j2, main.py.j2, config.py.j2, requirements.txt.j2, .env.example.j2, README.md.j2`). Contents contract:
  - `crew.py.j2`: CrewAI `Agent(role, goal, backstory)` per spec agent; `Task(description, expected_output, agent, context=depends_on)` per spec task; `Crew(...)` wired per pattern (sequential order / router conditional / report aggregation / approval `human_input=True` / watcher poll loop in main).
  - `config.py.j2`: LLM wiring through the copied-in provider wrapper (env-driven, MOCK_MODE switch to a `MockLLM` with canned schema-shaped responses).
  - `main.py.j2`: CLI arg for input, structured log line per task start/end (the exec-tier harness greps these), writes output artifacts.
  - `README.md.j2`: what was generated, what's stubbed, exact run commands.
- Tool stub template (inside `tools.py.j2`): real signature, typed args, docstring from spec `purpose`, `MOCK_MODE` canned payload, `# TODO: connect real <system>`.
- **Accept:** each template renders from a hand-written spec into `ast.parse`-clean files.

### 3.3 Built-in tools — **Model: S**
- `builtin_tools.py` real implementations: `read_file, write_file, http_get, parse_csv, write_markdown_report, send_message` (outbox/ folder stand-in for Slack/email). Each a CrewAI `@tool` with unit tests.
- **Accept:** built-in tool unit tests green.

### 3.4 Pattern selector — **Model: S**
- `selector.py`: deterministic from `flow.pattern`; if translation confidence low (pattern missing/ambiguous), LLM fallback scores spec against the 5 pattern descriptions; log selector confidence.
- **Accept:** 5 hand specs select correctly; a pattern-less spec triggers the LLM fallback path (mocked).

### 3.5 Golden render test — **Model: H**
- One hand-written WorkflowSpec per pattern → render → `ast.parse` every file, assert the spec's agent roles appear in `crew.py`.
- **Accept (phase DoD):** 5 pattern sets render clean; benchmark mapping documented; built-in tools tested.

---

## Phase 4 — Code Smith

### 4.1 Closed tool registry — **Model: F**
- `registry.py`: `resolve(tool_spec) -> BuiltinTool | StubPlan` — name/purpose match against built-ins; anything unresolved becomes an explicit MOCK_MODE stub. **Invariant: no code path emits an import or class name outside registry + templates.**
- **Accept:** a spec inventing `SlackNotifierTool` resolves to a stub with TODO, never an import.

### 4.2 Generator core — **Model: F**
- `generate/gapfill.py`: structure is deterministic template output; the LLM fills only bounded gaps — agent backstories, task prompt bodies, tool docstrings. Gap-fill prompt draft:
  ```
  Fill in ONLY the marked gap. Context: the workflow spec excerpt below.
  Write {gap_kind} for {target}. You MUST use the spec's own nouns
  (systems, artifacts, roles): {domain_nouns}. 2–4 sentences, no code,
  no imports, no new tool or system names.
  ```
- Post-fill re-validation: file still `ast.parse`s AND AST-diff shows **zero new imports** vs the skeleton.
- **Accept:** gap-filled files pass both checks; a fill that sneaks an import is rejected (test with a fault-injecting mock).

### 4.3 Manifest + project writer — **Model: S**
- `manifest.json` per project: source spec, pattern, tools resolved-vs-stubbed, assumptions carried over, LLM calls made. `writer.py`: idempotent to `generated/<slug>/`, refuses to overwrite non-generated dirs (checks for its own manifest), regeneration diffs against previous manifest.
- **Accept:** re-generation is a no-op diff on identical spec; a foreign directory is refused.

### 4.4 Pipeline wiring — **Model: F**
- `pipeline/graph.py`: LangGraph nodes `parse → lint → select_pattern → render → gap_fill → write` with typed state; errors accumulate in state, never raise past a node.
- **Accept:** a lint-failing spec lands as a structured pipeline error, not a traceback.

### 4.5 Integration tests — **Model: S**
- Two benchmark specs (one per category) generate end-to-end; AST walk asserts every import ∈ registry + stdlib + pinned deps.
- **Accept (phase DoD):** `w2a convert examples/workflows/ticket_triage.md` writes a complete project, imports AST-verified, stubs have TODOs, manifest records provenance; both category tests pass.

---

## Phase 5 — Proof Gate

### 5.1 Static tier — **Model: S**
- `static_tier.py`: `py_compile` every file, `ruff check`, AST import-allowlist re-verification. Typed report per check.
- **Accept:** seeded syntax-error project caught here.

### 5.2 Environment tier — **Model: S**
- `env_tier.py`: `uv venv` (fallback `python -m venv`), install generated `requirements.txt`, import every module in a subprocess — catches deps the dev env masks.
- **Accept:** seeded missing-dep project caught here.

### 5.3 Execution tier — **Model: S**
- `exec_tier.py`: run `main.py` with `MOCK_MODE=1`; assert exit 0, every spec task reached execution (grep the structured log lines), expected artifact exists (report file / outbox message / triage label).
- **Accept:** seeded runtime-crash project caught here; healthy project passes with artifacts.

### 5.4 Specificity check — **Model: S**
- `specificity.py`: extract domain nouns from the spec (proper nouns + tool/system/artifact terms); score their coverage in generated agent/task prompts; below threshold (default 0.6) → fail with "generic scaffolding" verdict listing missing concepts.
- **Accept:** seeded boilerplate project ("Agent 1 processes the input") fails with the right verdict.

### 5.5 Repair loop — **Model: S**
- `repair.py` as a LangGraph cycle: failure → error report (traceback + failing file + relevant spec slice) → LLM patches **only the failing file** → re-validate; max 3 iterations → honest failure report. Repair prompt: same shape as the gap-fill prompt plus the traceback; **every repair re-runs the AST import-allowlist check** (hallucinated imports sneak back here).
- `validation_report.json`: tier results, repair iterations, verdict `pass|pass_with_repairs|fail`; CLI prints it readably.
- **Accept (phase DoD):** all four seeded-defect projects caught by the correct tier; three auto-repaired within budget; the generic one fails with the right verdict; both Phase-4 projects validate `pass` in a fresh venv with mock-mode artifacts.

---

## Phase 6 — Field Trial (all tasks **Model: S**)

### 6.1 Full matrix run
- `convert` + `validate` on all 6 workflows. Build the generalization matrix in RESULTS.md: per workflow — lint-clean? correct pattern? tier reached? repair count? specificity score? mock artifact correct?
- **Accept:** matrix covers all 6; ≥5 reach `pass`/`pass_with_repairs`.

### 6.2 Real-mode runs
- **ticket triage** (folder of fake-but-realistic tickets) and **PR summarization** (real public PR diff via GitHub free API) run on the free-tier LLM end to end; outputs saved as demo artifacts.
- **Accept:** both crews complete in real mode with saved outputs.

### 6.3 Honest grading + failure queue
- Grade real outputs (sensible triage labels? summary reflects the diff?); record misses verbatim; tag every failure (ambiguity / tool-mapping / generic / template bug / harness bug) — Phase 7's work queue. Cross-category comparison written up (tool mixes, pattern distribution, repair rates).
- **Accept (phase DoD):** graded outputs + tagged failure list in RESULTS.md; the 6 spec outputs frozen as `tests/fixtures/specs/`.

---

## Phase 7 — Guard Rails

### 7.1 Work the failure queue — **Model: O**
- Each Phase-6 tagged item: fix + regression test; no drive-by fixes without tests.
- **Accept:** every queue item closed with a linked test.

### 7.2 Ambiguity hardening — **Model: O**
- Adversarial input suite: one-liner ("automate my standup"), contradictory (daily *and* on-demand), non-workflow ("make me rich"), mixed (two processes in one paragraph). Each must yield clarify-mode or a graceful structured refusal — never a confabulated spec.
- **Accept:** adversarial suite green.

### 7.3 Tool-mapping hardening — **Model: O**
- Fuzzy matcher in `registry.py` (normalized name + keyword/purpose overlap); ambiguous matches surface as a manifest question, not a guess. Test with 15 paraphrases ("ping the team channel", "file it in our tracker", "check the diff").
- **Accept:** ≥90% correct-or-asked on the 15.

### 7.4 Anti-generic + repair audit — **Model: O**
- Tune specificity threshold against Phase-6 data; add the diversity check (n-gram overlap cap between two different workflows' generated prompts). Audit every Phase-6 repair diff for hallucinated-import sneak-back; tighten the patch prompt if found.
- **Accept:** diversity test green; audit noted in DECISIONS.md.

### 7.5 Chaos tests + regression re-run — **Model: O**
- Pipeline chaos: LLM timeout mid-generation, malformed structured output at each node, empty template render → each lands as a clean pipeline-state error with a resumable checkpoint. Re-run the full Field Trial matrix; diff against Phase 6.
- **Accept (phase DoD):** chaos tests green; re-run matrix equal-or-better on every metric; zero regressions against frozen spec fixtures.

---

## Phase 8 — Showcase (all tasks **Model: H**; escalate the CI smoke test to S if it misbehaves)

### 8.1 CLI polish
- `w2a convert <file|-> [--interactive]` (interactive asks the clarifying questions live), `w2a validate <project>`, `w2a demo` (runs the two real-mode Field Trial workflows start to finish).
- **Accept:** all three commands work on a fresh clone with only an API key.

### 8.2 README + samples
- Hook ("a LangGraph app that writes CrewAI apps"), Mermaid pipeline diagram (parse→lint→pattern→render→gap-fill→validate→repair loop), two-framework rationale, honest-limitations from RESULTS.md. Commit two sample generated projects (one per category) with manifests + validation reports.
- **Accept:** README complete; samples browsable.

### 8.3 Demo + writeups
- Live-convert a never-before-seen workflow per category; record a GIF of the full loop. RESULTS.md final pass (matrix, before/after, failure gallery). DECISIONS.md: the four load-bearing ideas (deterministic/LLM boundary, closed registry, repair budget, specificity check).
- **Accept:** GIF committed; both docs final.

### 8.4 Repo hygiene + CI
- License, CONTRIBUTING-lite, GitHub Actions (ruff + unit tests + one mock-mode generation smoke test).
- **Accept (phase DoD):** `w2a demo` clean on fresh clone; CI green.

---

## Model routing summary

| Task | Model | | Task | Model | | Task | Model |
|---|---|---|---|---|---|---|---|
| 1.1 | H | | 3.3 | S | | 5.5 | S |
| 1.2 | S | | 3.4 | S | | 6.1–6.3 | S |
| 1.3 | S | | 3.5 | H | | 7.1 | O |
| 1.4 | S/H | | 4.1 | F | | 7.2 | O |
| 2.1 | O | | 4.2 | F | | 7.3 | O |
| 2.2 | S | | 4.3 | S | | 7.4 | O |
| 2.3 | O | | 4.4 | F | | 7.5 | O |
| 2.4 | O | | 4.5 | S | | 8.1–8.4 | H (→S if CI fights) |
| 3.1 | S | | 5.1–5.4 | S | | | |
| 3.2 | S | | | | | | |

Sequencing unchanged from PLAN.md: strictly 1→8; Phase 4 waits for lint-clean specs AND clean-rendering templates independently; if scope must be cut, cut pattern count (5→4) — never the deterministic/LLM boundary or the closed registry.
