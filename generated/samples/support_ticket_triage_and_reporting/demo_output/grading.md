# Real-mode run: 5 fake-but-realistic support tickets

Input: `sample_inputs/ticket_01.txt` … `ticket_05.txt` (hand-written, one per plausible category/urgency combination)
Command: `MOCK_MODE=0 python main.py sample_inputs/ticket_NN.txt --once` (Gemini `gemini-flash-lite-latest`, real LLM; `zendesk_connector`/`pagerduty_api` correctly stubbed — no real Zendesk/PagerDuty credentials)
Full raw transcript: `real_run_outputs.log`

| Ticket | Content (gist) | Expected urgency | Actual alert decision | Correct? |
|---|---|---|---|---|
| 01 | checkout down, "losing money", can't login | urgent | alerted via PagerDuty | yes |
| 02 | duplicate billing charge, "not urgent" | not urgent | not alerted, queued | yes |
| 03 | API pagination question, "not blocking" | not urgent | not alerted, queued | yes |
| 04 | export 500 error, "no rush" | not urgent | not alerted, queued | yes |
| 05 | data loss after nightly sync, "right now" | urgent | alerted via PagerDuty | yes |

**Urgency routing: 5/5 correct.** The urgency signal (keyword-driven per the spec's assumption: "down/broken/can't login/losing money") worked exactly as intended on realistic phrasing, including ticket 5 where the trigger word wasn't a literal keyword match but "data loss right now" was still correctly read as critical.

## Grading gap (harness limitation, not a workflow-logic failure)

`main.py` only prints the **last** task's raw output (`trigger_alert`'s "confirmation of alert"), not `classify_ticket`'s category label. The category (bug/billing/question) genuinely gets computed — ticket 1's final text says "the urgent **bug** report" confirming the classifier ran correctly — but there is no clean way to observe the category for tickets 2-4 without parsing noisy verbose ReAct transcript text. **This is a tagged failure**: the `scheduled_watcher` main.py template surfaces only the crew's final `.raw` output, discarding intermediate task results, which matters for any router-shaped workflow where the useful output isn't the last task in the chain. See RESULTS.md 6.3 for the root-cause tag.

## Other observations

- Every ticket's `classify_ticket` task tried calling the (correctly stubbed) `zendesk_connector` tool for ticket content even though the real text was already embedded directly in the task description via the `{input}` patch — the LLM defaults to reaching for a tool that exists rather than trusting inline context. Harmless here (it recovers and proceeds after the stub's `NotImplementedError`), but worth noting as an agent-prompting nuance, not a w2a defect.
- `generate_weekly_report` fires on every single `--once` invocation alongside `classify_ticket`, because the two are structurally disconnected in the task graph (the `orphan_task` lint warning from 6.1) — the watcher-pattern template re-runs every declared task on every poll pass rather than only the ones actually triggered by the event. For a real deployment this would generate five redundant "weekly reports" instead of one at week's end. Tagged for Phase 7.
- Windows console output is corrupted by `CrewAIEventsBus` handler errors — `'charmap' codec can't encode character '\U0001f527'` — because CrewAI's own emoji-decorated log lines hit Windows' default `cp1252` stdout encoding. Cosmetic only (the final `--- RESULT ---` text is unaffected), but makes verbose transcripts hard to read on Windows. Not a w2a-generated-code bug — it's CrewAI's own logging on this platform.
