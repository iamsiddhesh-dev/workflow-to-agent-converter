# Design Decisions

## Two frameworks, deliberately different

The converter and the code it generates want opposite things, so they use different frameworks:

- **The converter is a LangGraph pipeline** (parse → design → generate → validate → repair). The repair loop — feed validation errors back to the LLM and regenerate, bounded retries — is a conditional-edge cycle, which is exactly what LangGraph is for. This puts real orchestration engineering in the project, not just prompt plumbing.
- **Generated projects target CrewAI.** CrewAI's declarative surface (role/goal/backstory agents, task lists, `@tool` decorators) is small and regular — good for a code generator, because there are few structurally-wrong ways to emit it. Generating free-form LangGraph graphs for arbitrary business processes would multiply the failure surface for no benefit at demo scale.

One-line pitch: **a LangGraph app that writes CrewAI apps.**

## Provider choice: Gemini primary, Groq fallback

Both are free-tier, both have SDKs usable from the converter and from generated crews' `config.py`. Gemini AI Studio is primary; Groq is the fallback on 429/5xx or any Gemini exception, so a rate limit on one provider doesn't stall the whole pipeline.

**Model note (2026-07-17):** the AI Studio key provisioned for this project is a "new user" key, which is blocked from `gemini-2.5-flash` and `gemini-2.5-flash-lite` ("no longer available to new users" — a hard 404, not transient) and has zero free-tier quota on the `gemini-2.0-flash*` family. `gemini-flash-lite-latest` (a rolling alias to Google's current recommended lite flash model) works reliably and is the default in `llm.py`. If quota/availability shifts again, that's the one line to change (`DEFAULT_GEMINI_MODEL` in `src/w2a/llm.py`, and `W2A_GEMINI_MODEL` env var for generated crews).

## Zero-cost validation

`MOCK_MODE` runs generated crews against a `MockLLM` with canned, schema-shaped responses, so the execution-tier validation and repair loop cost zero tokens. Real-mode runs (Phase 6 Field Trial) are the only place actual LLM calls hit generated projects during validation.

## Phase 7 — Guard Rails: audit findings and scope calls

**Repair-loop audit (hallucinated-import sneak-back).** Reviewed both Phase 6 repairs (`review_routing`, `bug_triage`) — both were specificity-tier retries (a re-run of gap-fill with missing concepts spelled out), and both landed clean: the AST import-diff gate inside `gap_fill()` re-verifies every filled file against the skeleton's own imports regardless of which repair strategy triggered it, and neither repair introduced anything new. No sneak-back found in the real data. Added `test_repair_loop_rejects_sneaked_import` (`tests/test_validate.py`) as a standing regression: a fake patch LLM that "fixes" a syntax error but also adds an unrelated import must be rejected outright (`applied=False`, verdict stays `fail`), proving the same gate that guards gap-fill also guards `_repair_file_with_llm` (the static/exec repair path) — it was previously only proven for the gap-fill path itself.

**Specificity threshold (0.6) kept, not raised.** Phase 6's six real scores (0.60–0.85) already cleared it, with `ticket_triage` sitting right at the line. Rather than raise the numeric bar, Phase 7.1 #7 tightened what counts as a "domain noun" in the first place (`src/w2a/spec/textutils.py`'s expanded stopword list, sourced from the actual noise words — `based`, `call`, `engineer`, `qualifies` — observed in Phase 6's `missing concepts` lists) so the metric measures the intended thing more precisely. A cleaner denominator is the right fix for a borderline score; moving the threshold would have been tuning the goalpost instead of the measurement.

**Diversity check added as a standalone cross-project tool, not a per-project tier.** `src/w2a/validate/diversity.py` compares the LLM-authored prose (agent backstories, task/tool descriptions — exactly gap-fill's three fill kinds, extracted via AST from `crew.py`) between two *different* generated projects, deliberately excluding the templated scaffolding that's identical by design across any two projects sharing a pattern. It isn't wired into `run_validation` because that function validates one project in isolation — diversity is inherently a fleet-level question ("do two *different* workflows read the same"), so it belongs at the Field Trial re-run level (RESULTS.md), not the per-project pass/fail gate.

**`git_diff`/`github_api` builtin: deliberately out of scope for Phase 7.** Phase 6's cross-category finding was that the closed tool registry is ops-shaped (file I/O, HTTP GET, CSV, markdown report, outbox) with no dev/eng analog, so every GitHub/git-diff/bug-tracker mention across the three dev/eng benchmarks fell through to a stub. Phase 7.3 hardened the *fuzzy matcher* (paraphrase coverage, tie-breaking into an honest stub instead of a guess) but did not add new builtin capabilities — that's an expansion of the registry's surface area, not a guardrail, and risks exactly the kind of scope creep PLAN.md's sequencing notes warn against ("if scope must be cut, cut pattern count, never the deterministic/LLM boundary or the closed registry" — adding a real GitHub API client is the kind of thing that *should* go through the same rigor as the original five builtins, not get bolted on inside a hardening phase). Left as a documented, explicit gap for a future phase rather than a silent one.

## Phase 8 — the four load-bearing ideas

Four decisions separate this from a toy code generator; each earlier section above is one of them in detail, restated together because this is the thing worth remembering about the project:

1. **Deterministic-template / LLM-gap-fill boundary.** Structure (agent wiring, task graph, `Crew(...)` assembly) is always rendered from a Jinja2 template, never LLM free-writing a whole file. The LLM only fills bounded prose slots — backstories, task descriptions, tool docstrings — each re-verified to still `ast.parse` and to introduce zero new imports (`gapfill.py`'s AST-diff gate). This is why a translation failure degrades to "generic but running" instead of "doesn't compile."
2. **Closed tool registry.** Every tool a spec mentions resolves to a real builtin or an explicit `MOCK_MODE` stub with a TODO — there is no code path that lets the generator emit an import or class name it invented. Hallucinated APIs (`SlackNotifierTool`, `import jira_client`) are structurally impossible, not just discouraged by prompting.
3. **Bounded repair loop.** Validation failures feed back to the LLM as a patch-only-the-failing-file request, capped at 3 iterations, with the same import-allowlist gate re-run on every attempt. No infinite loops, no silent success claims — a spent budget produces an honest `fail` verdict with the real error, not a retry that quietly gives up.
4. **Specificity check.** Passing static/env/exec tiers proves code *runs*; it doesn't prove the output is a real automation and not "Agent 1 processes the input." The specificity tier scores domain-noun coverage against the spec's own vocabulary and fails generic scaffolding outright, with a repair path that re-runs gap-fill against the missing concepts.
