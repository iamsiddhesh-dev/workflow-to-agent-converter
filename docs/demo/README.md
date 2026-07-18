# Demo capture

This environment is a headless CLI session with no screen-recording tool, so the demo capture here is real terminal transcripts from actual runs (real LLM calls, `MOCK_MODE` unset) rather than a GIF. Every file below is unedited output from the commands named.

- **`w2a_demo_transcript_summary.txt`** — a trimmed transcript of `w2a demo` (convert → validate → real run for both `ticket_triage` and `pr_summary`; see the main [README](../../README.md) for the command). Verbose CrewAI console UI is stripped; the convert/validate summary lines and each task's final result are kept. Both projects validated `pass` with 0 repairs; `pr_summary`'s console showed a one-off pattern-selector LLM-fallback structured-output validation error on this run (harmless — it fell back to the spec's declared pattern and validated clean), left in as an honest artifact rather than re-run until it disappeared.
- **`novel_ops_description.md`** / **`novel_deveng_description.md`** — the two never-before-seen workflow descriptions used for the Phase 8 generalization check (RESULTS.md, "8.A"): expense-report approval routing (ops) and CI flaky-test triage (dev/eng). Neither existed anywhere in this repo before this pass.
- **`novel_ops_convert_transcript.txt`** / **`novel_deveng_convert_transcript.txt`** — full unedited `w2a convert <file> --interactive` output for each, including the ambiguity-gate questions asked and the human-readable spec summary.
- **`novel_ops_validate_transcript.txt`** / **`novel_deveng_validate_transcript.txt`** — full unedited `w2a validate <project>` output for each; both reached `pass` with zero repairs.

See [RESULTS.md](../../RESULTS.md#phase-8--showcase-generalization-on-never-before-seen-input-and-w2a-demo) for the write-up these transcripts back.
