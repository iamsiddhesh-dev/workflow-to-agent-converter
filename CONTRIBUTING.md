# Contributing

This is primarily a portfolio project, but issues and PRs are welcome.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in GEMINI_API_KEY and/or GROQ_API_KEY for anything that hits a real LLM
```

## Before opening a PR

```bash
ruff check src tests
pytest -q --ignore=tests/test_validate.py   # fast, no key, no fresh venv — same as CI
```

The full suite (`pytest`) additionally exercises the environment-tier validator (creates real venvs, installs generated `requirements.txt`) and a handful of real-LLM acceptance tests gated behind `GEMINI_API_KEY`/`GROQ_API_KEY`; it takes several minutes and isn't run in CI.

## Ground rules

- No drive-by fixes without a regression test — see `RESULTS.md`'s tagged failure queues for the pattern this project follows (root cause tag + fix + linked test).
- The deterministic-template/LLM-gap-fill boundary and the closed tool registry are load-bearing (see `DECISIONS.md`); changes that blur them need a strong justification.
- Keep `tests/fixtures/specs/*.json` passing lint-clean, or update them deliberately — they're regression fixtures for the translator, not incidental output.
