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
