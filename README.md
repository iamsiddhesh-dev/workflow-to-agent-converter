# Workflow-to-Agent Converter

A LangGraph app that writes CrewAI apps: plain-language business process in → a real, runnable multi-agent project out (agent roles, tool stubs, execution flow — code, not a text plan).

See [PLAN.md](PLAN.md) for the phase-by-phase roadmap and [DETAILED_PLAN.md](DETAILED_PLAN.md) for task-level breakdowns, and [DECISIONS.md](DECISIONS.md) for the architecture rationale.

## Status

Phase 1 (Blueprint) in progress — repo scaffold, LLM provider wrapper, CrewAI hello-world smoke test, and benchmark workflow descriptions.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
cp .env.example .env  # fill in GEMINI_API_KEY / GROQ_API_KEY
```

## Try the smoke tests

```bash
pytest tests/test_llm_smoke.py -v
python examples/hello_crew/main.py
```
