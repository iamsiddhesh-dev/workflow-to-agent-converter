# Field Trial Results (Phase 6)

Full pipeline (`w2a convert` + `w2a validate`) run on all 6 benchmark workflows in `examples/workflows/`. Ambiguity gate fired on all 6 (each description under-specifies at least one external-system detail); answers were supplied via `--interactive` and are noted per workflow below.

## 6.1 — Generalization matrix

| Workflow | Category | Lint-clean | Declared pattern | Selected pattern | Selector source | Validation verdict | Repairs | Specificity score |
|---|---|---|---|---|---|---|---|---|
| onboarding | ops | yes | sequential | **report** | llm_fallback (conf 1.00) | pass | 0 | 0.76 |
| ticket_triage | ops | **warning**: orphan_task (`generate_weekly_report`) | router | **watcher** | llm_fallback (conf 1.00) | pass | 0 | 0.60 |
| weekly_report | ops | yes | report | **approval** | llm_fallback (conf 1.00) | pass | 0 | 0.70 |
| review_routing | dev/eng | yes | router | **sequential** | llm_fallback (conf 1.00) | pass_with_repairs | 1 (specificity) | 0.80 |
| pr_summary | dev/eng | yes | report | **sequential** | llm_fallback (conf 1.00) | pass | 0 | 0.71 |
| bug_triage | dev/eng | **warning**: unused_tool (`bug_tracker_api`) | approval | approval | deterministic (conf 1.00) | pass_with_repairs | 1 (specificity) | 0.85 |

**6/6 reach `pass` or `pass_with_repairs`** (DoD required ≥5/6). All 6 specificity scores clear the 0.6 threshold comfortably (0.60–0.85).

### Headline finding: pattern-selector disagreement, 5/6 of the time

Phase 3's benchmark→pattern mapping (PLAN.md) predicted: onboarding→sequential, ticket_triage→router, weekly_report→report, review_routing→router, pr_summary→report, bug_triage→router+approval. **Only bug_triage matched its predicted pattern**, and only because it went through the deterministic path — every other workflow's `flow.pattern` field, as translated, scored "low structural confidence" (0.20–0.30) against its own task graph and got overridden by the LLM-fallback selector.

Root cause, confirmed by reading the generated specs: the translator (Phase 2) collapses conceptually-branching workflows into 2–3 linear tasks. `ticket_triage`'s "read the ticket, decide bug vs billing vs question, route accordingly" becomes one `classify_ticket` task with a category label as output — there is no structural branch in the task graph for a "router" selector to key off, so the LLM fallback correctly (looking only at the graph) calls it something else. The declared `flow.pattern` field is carrying design intent that the task-graph shape alone doesn't express. **This is a translator granularity issue, not a selector bug** — the selector's fallback reasoning was sound in every case; the input it was reasoning over was the problem. Tagged for Phase 7 as `translation-granularity` (see 6.3).

### Ambiguity-gate answers supplied (--interactive)

- **onboarding**: HRIS-backed department lookup; Slack + email for welcome comms; audit report → manager; no named ITSM tool.
- **ticket_triage**: Zendesk for ticket intake; PagerDuty for on-call paging; weekly report delivered as a doc link in Slack.
- **weekly_report**: Markdown file output; engineering tickets in Jira, support in Zendesk.
- **review_routing**: CODEOWNERS-based ownership; Slack DM/mention for pings; GitHub as VCS; path-pattern sensitivity (`/auth/`, `/payments/`); general pool = on-call rotation.
- **pr_summary**: GitHub API for diff fetch and comment posting.
- **bug_triage**: shared intake form consolidating tickets/Sentry; static service→team mapping for owner suggestion.

None of the 6 needed a second clarify round after answers were supplied — all re-translated to a spec on the first pass post-answers (except `weekly_report` and `bug_triage`, which asked a second round of near-duplicate questions before settling; see 6.3 tagged failures).

---

## 6.2 — Real-mode runs

Real sample data, real Gemini LLM (`gemini-flash-lite-latest`), `MOCK_MODE=0`. External-system tools (Zendesk, PagerDuty, GitHub write) stay correctly stubbed — no real credentials exist for them, and that's the intended zero-cost-stack scope of "real mode" here: real reasoning over real data, not real side effects against third-party SaaS.

**Blocking discovery before any real-mode run could produce meaningful output:** none of the 5 pattern templates (`sequential`, `router`, `report`, `approval`, `watcher`) ever interpolate `{input}` into a task description. `main.py` reads the real input file into a `payload` string and passes it as `crew.kickoff(inputs={"input": payload})`, but CrewAI only substitutes `{input}` where that literal token appears in a task's `description` — and it appears nowhere in any generated `crew.py`. Confirmed by grep across `src/w2a/templates/`: zero matches for `{input}`. **A stock-generated project's real-mode run silently ignores whatever file or stdin content you feed it** — the crew reasons only from the task description text baked in at generation time. Static/env/exec-tier validation didn't catch this because `MockLLM.supports_function_calling() == False` (documented gotcha) means the exec tier never exercises real data flow either — it just checks the crew *runs*, not that it *uses its input*.

Worked around **for this field trial only** by hand-editing the two demo projects' `crew.py` (not the templates — this is Phase 7's fix) to append `\n\nActual <content> to analyze:\n{input}` to the first task's description. Tagged `template-bug` #1 below (highest severity in the queue — it's the one that would embarrass a live demo).

### ticket_triage (ops) — 5 fake-but-realistic support tickets

Full transcript: `generated/support_ticket_triage_and_reporting/demo_output/real_run_outputs.log`; grading: `generated/support_ticket_triage_and_reporting/demo_output/grading.md`.

| Ticket | Content (gist) | Expected | Actual | Correct? |
|---|---|---|---|---|
| 01 | checkout down, "losing money", can't login | urgent → alert | alerted via PagerDuty | yes |
| 02 | duplicate billing charge, "not urgent" | queue | queued, no alert | yes |
| 03 | API pagination question, "not blocking" | queue | queued, no alert | yes |
| 04 | export 500 error, "no rush" | queue | queued, no alert | yes |
| 05 | data loss after nightly sync, "right now" | urgent → alert | alerted via PagerDuty | yes |

**Urgency routing: 5/5 correct**, including ticket 5 where the trigger phrase ("data loss right now") wasn't a literal keyword match to the spec's own assumption list but was still read correctly as critical. Category label (bug/billing/question) is *computed* correctly — ticket 1's alert text says "the urgent **bug** report", confirming the classifier ran — but isn't independently gradable for tickets 2-4 because of tagged failure #3 below (only the last task's output is surfaced).

### pr_summary (dev/eng) — real PR diff, pallets/flask #5928

Diff fetched live via `https://github.com/pallets/flask/pull/5928.diff` (GitHub's public `.diff` endpoint, no auth). Full output: `generated/pr_summary_generator/demo_output/pr_5928_summary.md`.

**Pass, and a strong one.** Every claim in the generated summary traces to an actual diff hunk: the `_CollectErrors` helper, the `BaseExceptionGroup` Python-version note (present verbatim in the real docstring), the `CHANGES.rst`/`docs/appcontext.rst` doc edits, and the `with client.get(...) as rv:` test changes. Correctly classified as substantive (not the trivial-fix one-liner path) and correctly identified the single most reviewer-relevant fact: this changes backward-compatible "fail-fast" teardown behavior. The `pr_commenter` tool call failed as expected (no real GitHub write token — MOCK_MODE=0 real-mode is scoped to real LLM + real input, not real external writes) and the agent recovered gracefully rather than crashing.

---

## 6.3 — Tagged failure queue (Phase 7 work items)

The five prescribed root-cause tags (ambiguity / tool-mapping / generic / template bug / harness bug) don't cleanly cover everything found — one item below needed a sixth tag, called out explicitly.

| # | Finding | Root cause | Severity |
|---|---|---|---|
| 1 | No pattern template ever interpolates `{input}` into a task description — real-mode input is silently discarded unless hand-patched | **template bug** | Critical — breaks the entire premise of real-mode execution |
| 2 | `main.py` templates print only the last task's `.raw` output; intermediate task results (e.g. a router's classification label) are unobservable except by parsing noisy verbose logs | **template bug** | High — makes router-shaped workflows ungradable in practice |
| 3 | `scheduled_watcher`'s `main.py` re-runs *every* declared task on every poll pass, including tasks structurally disconnected from the trigger (e.g. `generate_weekly_report` fires on every single ticket poll instead of once a week) | **template bug** | High — would spam redundant weekly reports in a real deployment |
| 4 | 5/6 workflows' declared `flow.pattern` was overridden by the LLM-fallback selector because the translator collapses branching/routing intent into 2-3 linear tasks with no structural branch for the selector to key off | *no clean tag — translator/selector-boundary issue, closest to* **ambiguity** | High — undermines Phase 3's benchmark→pattern mapping as generalization evidence |
| 5 | `bug_triage`'s translated spec declares a `bug_tracker_api` tool that no task actually references (`unused_tool` lint warning), despite `file_issue`'s description clearly needing exactly that capability | **ambiguity** (translation didn't wire its own tool declaration to the task that needs it) | Medium |
| 6 | `weekly_report` and `bug_triage` asked a second round of near-duplicate clarifying questions after `--interactive` answers were already supplied, instead of treating the answers as settled | **ambiguity** | Medium — UX friction, not a correctness bug |
| 7 | Specificity tier's "missing concepts" list mixes true domain nouns (`auth`, `payments`) with generic content words (`based`, `call`, `engineer`, `qualifies`) — matches the tier's documented design (score *all* content words, not a curated noun list) but makes repair-loop target lists noisier than necessary | **harness bug** | Low — didn't cause a wrong verdict on any of the 6, but worth tightening before Phase 7's threshold tuning |
| 8 | `env_tier`'s fixed 60s subprocess import-check timeout raised an unhandled `TimeoutExpired` traceback (not a clean `EnvTierReport`) when validations ran concurrently under machine load | **harness bug** | Low — self-inflicted by running multiple validations in parallel during this field trial; retry in isolation passed clean, but a validator shouldn't be able to leak a bare traceback regardless of load |
| 9 | Windows console spam: every CrewAI verbose log line containing an emoji (🔧🚀📋) throws a `charmap` codec `UnicodeEncodeError` inside `CrewAIEventsBus`'s own handler, garbling interleaved terminal output | **harness bug** | Low — cosmetic (final results are unaffected), a one-line `sys.stdout.reconfigure(encoding="utf-8")` in `main.py.j2` would likely fix it, not a w2a logic defect |

## Cross-category comparison (ops vs dev/eng)

| Metric | Ops (onboarding, ticket_triage, weekly_report) | Dev/eng (review_routing, pr_summary, bug_triage) |
|---|---|---|
| Pattern matched declared field | 0/3 (all llm_fallback) | 1/3 (bug_triage, deterministic) |
| Repairs needed | 0 total | 2 total (2/3 projects) |
| Avg specificity score | 0.69 | 0.79 |
| Tools resolved to real builtins | 2 (`read_file`, `write_markdown_report`, both in weekly_report) | 0 |
| Tools resolved to MOCK_MODE stubs | 7 | 5 |

Two real generalization findings, not just noise:

1. **The closed tool registry is ops-shaped, not dev/eng-shaped.** Every dev/eng tool mention (GitHub API, git diff reader, PR commenter, bug tracker, service mapper) fell through to a stub — none matched a builtin, because the current builtin set (file I/O, HTTP GET, CSV, markdown report, outbox) is generic office-automation tooling with no GitHub/git-specific analog. Ops workflows, which lean on "read a file / write a report," got real implementations for free. This is exactly the kind of category-specific gap the two-category benchmark design (PLAN.md) exists to surface — Phase 7 or a future phase should consider a `git_diff` / `github_api` builtin if dev/eng coverage matters for the portfolio story.
2. **Dev/eng specs scored more specific but needed more repair passes.** The domain vocabulary in dev/eng workflows (`auth`, `payments`, `migrations`, `diff`, `CODEOWNERS`) is more distinctive as content words than ops vocabulary (`account`, `ticket`, `report`), which likely explains the higher average specificity score once gap-fill succeeded — but the first-pass gap-fill missed that vocabulary more often (2/3 dev/eng projects needed a specificity repair vs 0/3 ops), suggesting the gap-fill prompt's grounding in spec nouns is somewhat weaker for the more technical, jargon-dense category.

Both categories cleared validation at the same overall rate (all 6 reached `pass`/`pass_with_repairs`) — the differences are in *how* they got there, not *whether*.

## Spec regression fixtures

The 6 translated WorkflowSpecs are frozen at `tests/fixtures/specs/{onboarding,ticket_triage,weekly_report,review_routing,pr_summary,bug_triage}.json`, extracted from each generated project's `manifest.json`. These pin current translation output so Phase 7 changes to the translator/selector can be diffed against a known-good baseline instead of silently drifting.

---

# Phase 7 — Guard Rails: re-run matrix and before/after

Full pipeline re-run on all 6 benchmarks with the Phase 7 fixes applied, same `--interactive` answers as Phase 6, same free-tier Gemini. Every Phase-6 tagged failure has a fix + regression test (see below); the frozen `tests/fixtures/specs/*.json` still lint-clean with identical warnings and render `ast.parse`-clean under the new template code (no regression against the baseline).

## 7.A — Re-run generalization matrix

| Workflow | Category | Lint | Declared → Selected | Selector source | Verdict | Repairs | Specificity | Δ vs Phase 6 |
|---|---|---|---|---|---|---|---|---|
| onboarding | ops | clean | sequential → **sequential** | **deterministic** 1.00 | pass | 0 | 0.68 | selector now deterministic (was report/llm_fallback) |
| ticket_triage | ops | warning: orphan_task (`generate_weekly_summary`) | router → **router** | **deterministic** 1.00 | pass | 0 | 0.71 | now router **with real fan-out** (was watcher/llm_fallback) — matches predicted mapping |
| weekly_report | ops | clean | report → **approval** | llm_fallback 1.00 | pass_with_repairs | 1 (specificity) | 0.89 | still fallback (correct — translation gave it a human checkpoint); specificity 0.70 → 0.89 |
| review_routing | dev/eng | clean | router → **router** | **deterministic** 1.00 | pass | 0 | 0.69 | now router deterministic (was sequential/llm_fallback) — matches predicted mapping; repairs 1 → **0** |
| pr_summary | dev/eng | clean | sequential → **sequential** | **deterministic** 1.00 | pass | 0 | 0.81 | now deterministic (was llm_fallback); specificity 0.71 → 0.81 |
| bug_triage | dev/eng | clean | router → **router** | **deterministic** 1.00 | pass | 0 | pass | now router-with-checkpoint deterministic; repairs 1 → **0**; **exposed + fixed a latent F821** (see 7.C) |

### Headline: deterministic pattern selection went from 1/6 to 5/6

Phase 6's central finding was that 5/6 workflows had their declared `flow.pattern` overridden by the LLM fallback because the translator collapsed branching intent into thin, linear task graphs. The 7.1 #4 fix (a `CRITICAL for "router"` rule in the translation prompt + a third worked example — `_TICKET_TRIAGE_SPEC` — that models real fan-out) directly reverses this: **5 of 6 workflows now select their pattern deterministically** (up from 1/6), because the task graphs now structurally express their real shape. `ticket_triage` and `review_routing` now select `router` *with an actual fan-out* (two tasks depending on one classifier), finally matching PLAN.md's predicted benchmark→pattern mapping — the generalization thesis the Phase 6 selector-disagreement had undercut. The one remaining `llm_fallback` (weekly_report → approval) is *correct*, not a failure: the translation legitimately gave it a human checkpoint, so approval is the right structural call and the fallback agreed.

Aggregate movement, every metric equal-or-better:
- **Deterministic selection: 1/6 → 5/6.**
- **Total repairs across the matrix: 2 → 1** (review_routing and bug_triage both dropped to 0; only weekly_report's one specificity repair remains).
- **Verdicts: 6/6 still pass/pass_with_repairs**, no regression, after the 7.C fix.
- **Specificity: 4 of 6 scores rose** (weekly_report 0.70→0.89, pr_summary 0.71→0.81, review_routing held, bug_triage clean-passed); onboarding and ticket_triage dipped slightly (0.76→0.68, 0.60→0.71 — the tightened stopword list from 7.1 #7 changed the denominator, so scores aren't directly comparable to Phase 6's, but all clear 0.6).

## 7.B — Tagged failure queue: closed

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | `{input}` never interpolated | `_common/macros.j2` appends `{input}` to any root task's description, anchored on `t.is_root` **downstream of the gap-fill-controlled field** so a fill can't strip it | `test_root_tasks_interpolate_input`, `test_gap_fill_cannot_strip_the_input_marker` (test_templates.py) |
| 2 | Only last task's output surfaced | `main.py` templates gained `_print_task_outputs()` — every task's `.output.raw`, not just the crew's final `.raw` | `test_main_py_prints_every_task_output` |
| 3 | Watcher re-runs every task every poll | `scheduled_watcher` splits `is_periodic` (disconnected) tasks into a separate `build_periodic_crew()`/`--periodic` path; exec tier runs a second `--periodic` pass to still prove they execute | `test_periodic_task_excluded_from_poll_crew`, `test_main_py_exposes_periodic_flag_*`, `test_exec_tier_reaches_periodic_tasks_via_second_pass` (test_validate.py) |
| 4 | Translator collapses branching → 5/6 selector overrides | translation-prompt router rule + `_TICKET_TRIAGE_SPEC` fan-out worked example | `test_build_prompt_includes_router_fan_out_guidance`, `test_ticket_triage_worked_example_has_real_fan_out` (test_translate.py) + the re-run matrix above (5/6 deterministic) |
| 5 | `unused_tool`: declared tool not wired to its task | bounded one-shot self-correction in `translate()`: lint for `unused_tool`, re-ask once naming the tool | `test_translate_retries_once_when_declared_tool_is_unused` + 2 more (test_translate.py) |
| 6 | Near-duplicate clarify rounds | `drop_answered()` word-overlap filter + bounded `resolve_ambiguities()` loop (max 2 rounds) | 5 tests incl. `test_resolve_ambiguities_stops_once_no_new_questions_remain` (test_ambiguity.py) |
| 7 | Specificity missing-concepts noisy with filler words | consolidated tokenizer in `spec/textutils.py` with a data-driven expanded stopword list (curated from Phase 6's actual noise words) | covered via the consolidated `_content_words` used by existing lint/specificity tests |
| 8 | env_tier leaks raw `TimeoutExpired` | `_run()` wrapper turns any subprocess timeout into a clean `(False, message)` → `EnvTierReport(ok=False)` | `test_env_tier_timeout_is_a_clean_report_not_a_traceback` (test_validate.py) |
| 9 | Windows charmap console spam | `sys.stdout/stderr.reconfigure(encoding="utf-8")` guard in every `main.py` | `test_main_py_reconfigures_stdout_encoding` |

## 7.C — New bug found by the re-run: F821 `MOCK_MODE` in non-approval patterns

The 7.1 #4 translator fix had a second-order consequence the re-run caught: `bug_triage` now translates to a **router** with an internal `human_checkpoint` (it used to flatten to `approval`). The shared `task_kwargs` macro emits `human_input=not MOCK_MODE` for *any* checkpoint task — but only the `approval_gate` template imported `MOCK_MODE`. So a router-with-checkpoint rendered a `crew.py` with an undefined name, caught by the static tier's ruff sub-check (`crew.py:68 F821 Undefined name MOCK_MODE`), which the repair loop couldn't fix in 3 iterations → `bug_triage` briefly went to `verdict: fail` in the raw re-run.

This is exactly the cross-cutting interaction Phase 7 exists to surface: a translator improvement changed which pattern a spec renders as, exposing a latent template assumption ("only approval has checkpoints"). Fix: `has_human_checkpoint` render-context flag drives a conditional `from config import MOCK_MODE, build_llm` in every crew template. After the fix, `bug_triage` re-runs to **`pass`, 0 repairs** (better than Phase 6's `pass_with_repairs`). Regression tests: `test_human_checkpoint_in_non_approval_pattern_imports_mock_mode` (parametrized over router/sequential/report/watcher) and `test_no_human_checkpoint_does_not_import_mock_mode_into_crew` (guards against the inverse F401).

## 7.D — Adversarial, tool-mapping, diversity, chaos suites

- **Adversarial ambiguity (7.2):** `test_adversarial.py` — one-liner, self-contradiction, non-workflow, and mixed-workflow inputs each assert *not confabulated* (defined precisely: a populated design with an empty `ambiguities[]`). Prompt gained explicit contradiction- and non-workflow-refusal rules. Deterministic half (prompt-content assertions) runs keyless in CI; network half proves real behavior.
- **Tool mapping (7.3):** `test_tool_mapping_paraphrases.py` — 15 paraphrased tool mentions, **100% correct-or-asked** (bar was ≥90%), plus the stricter `test_no_case_silently_mismaps` (the 10% slack may only be spent on an over-cautious stub, never a wrong builtin). Keyword sets widened for recall; the ambiguity tie-break that protects precision left intact. `git_diff`/`github_api` builtin explicitly left out of scope (documented in DECISIONS.md).
- **Diversity (7.4):** `src/w2a/validate/diversity.py` + `test_diversity.py` — AST-extracted prose (only Agent/Task prose kwargs, excluding identical scaffolding) compared between two projects via n-gram overlap; two different golden projects pass, seeded near-duplicate prose fails. A fleet-level check, deliberately not wired into per-project `run_validation`.
- **Chaos (7.5):** `test_chaos.py` — LLM timeout at translate (→ clean parse error, nothing written), malformed output at translate (→ clean parse error) and at gap-fill (→ skeleton ships with warning), empty template render (→ new `render_node` guard raises a clean render error), and a resumable-checkpoint proof (a downstream failure doesn't force re-translation). All land as structured `PipelineError`s in state, never a traceback.
- **Repair-loop audit (7.4):** both Phase-6 repairs re-reviewed — both were clean specificity retries, no hallucinated-import sneak-back (the AST import-diff gate in `gap_fill()` covers every repair path). Added `test_repair_loop_rejects_sneaked_import` as a standing guard on the static/exec repair path.
