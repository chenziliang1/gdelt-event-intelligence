# Agent Evaluation Results Summary

Run against a live backend (`docker compose up backend`) with the full 2024 North America GDELT dataset restored (17,480,236 events), Ollama running qwen2.5:3b locally, and a real Kimi/Moonshot API key for the remote LLM fallback. Raw per-item results in `agent_eval_results.json`. Question set and expected outcomes in `agent_eval_set.json`.

## Overall

13 / 24 passed, 10 failed, 1 skipped by design (no strict assertion). This is the first time this agent has been scored against a written question set; no prior baseline exists to compare against.

## Findings that matter (ranked by how much they'd affect a real user)

### 1. Relative-date queries anchor to the real wall-clock date, not to the dataset's actual coverage window

`overview-02` ("how has California been this month") and `reldate-02` ("what happened yesterday in Illinois") both resolved their date range around **2026-07-13**, the actual current server date, even though the entire dataset is 2024 events only. A query like this would return zero real rows every time, regardless of phrasing, because the system is reasoning about "now" instead of "now, relative to the data this system actually has." This is the most consequential finding in the set: it fully breaks an entire category of otherwise reasonable questions ("this month", "yesterday", "recently") against a system whose backing data is a static historical extract.

### 2. "Last week" and other relative-date phrases resolve to wildly wrong window lengths, even on the confident (non-fallback) path

`reldate-01` ("protests in Texas last week") resolved to a 131-day window (roughly 2024-01-01 to 2024-05-11), confirmed reproducible on a second run. `reldate-03` ("events in the past 3 days in New York") resolved to a 138-day window instead of 3 days. Both of these came back with `confidence: high`, meaning this is not just a symptom of the regex-fallback path; the router (or the remote LLM plan step) is confidently producing an incorrect date range, not failing to produce one. Root cause, confirmed by reading the actual Planner code rather than guessing: the local Ollama router's system prompt never receives a reference/anchor date to compute "last week" against, and neither the router's output nor the rule-based plan validates that a "last week"-style query actually produced a roughly 7-day (or 1-day, or 3-day) window before accepting it. A separate `parse_time_hint()` helper elsewhere in the codebase anchors relative dates to a different fixed date (2024-01-31) than whatever produced these two results, so there are at least two inconsistent notions of "relative to when" already in the code.

### 3. Direct event-ID lookups never actually reach the `event_detail` tool

All three direct-lookup-style queries (`detail-01` "tell me about event 1150442224", `detail-02` using the `EVT-2024-01-09-1150442224` fingerprint format, `detail-03` a nonexistent ID) were classified as generic `events` search rather than `event_detail`, even though `query_event_detail` in `core_queries.py` explicitly implements three fingerprint-format branches for exactly this use case. In practice, natural-language event-ID lookup does not currently work through the Planner at all; the underlying `event_detail` capability exists in the data layer but the router never selects it for these phrasings.

### 4. Two real, reproducible instances of the low-confidence (regex-fallback) path actually firing, both leading to misrouting

`brief-01` ("give me today's daily brief") and `overview-01` ("give me a regional overview of Texas this year") both returned `confidence: low` and were both misrouted to a generic `events` search instead of `daily_brief` / `regional_overview` respectively. This is a live, reproducible confirmation that the fallback path (triggered when the local Ollama router's structured extraction fails, per the `extract_context` bug where `query_text` can be null with no fallback reference to the original query) is not just a latency/cost concern; when it fires, it also demonstrably picks the wrong tool more often than the router's happy path does in this sample.

### 5. Minor: "what's hot right now" (no date given) is classified as generic `top_events` rather than `hot_events`

Lower severity than the above; this is a plausible product decision either way (both tools return similar shapes of "what's notable" data), not clearly a bug, just an inconsistency worth a one-line note.

## What worked correctly

- The regex fast path for greetings and off-topic input (`greet-01/02/03`) works exactly as designed: no Ollama call, no DB call, sub-30ms response.
- Structured queries that include a region, a specific month, and a distinctive free-text component (`top-01`, `top-02`, `confidence-probe-01/02/03`, `search-01`, `search-02`) consistently routed correctly with `confidence: high` and returned real, correct 2024 event data.
- Date-anchored `hot_events` and `daily_brief` queries that include an explicit date (`hot-02`, `brief-02`) routed correctly.
- The previously-missing `event_fingerprints` table (a schema-initialization gap, not a dump problem, per code review) no longer causes a hard 500; `keyword-fts-01` completed successfully, though as expected its headline/summary fields are empty since the population ETL has not been run separately from the schema fix.

## What this eval set deliberately does not cover

- Map/geo-specific queries: the restored database's `ActionGeo_Point` geometry column was being regenerated from raw lat/long at the time this run was executed (a separate, one-time data-repair step unrelated to the agent itself), so geo-heavy questions were out of scope for this pass.
- A rigorous per-item human-graded answer-quality score (as opposed to tool-selection and date-range correctness). That is a reasonable next iteration of this harness, not something this multi-hour pass was scoped to do.

## Direct answer to "why should I trust this number"

This is not a synthetic or hand-waved evaluation. Every item was run against the live `/api/v1/analyze` endpoint, backed by the actual restored 2024 GDELT dataset (not a stub or mock), the actual local Ollama qwen2.5:3b instance, and the actual remote LLM provider configured for this project. The five findings above are traceable to specific request/response pairs recorded in `agent_eval_results.json`, not to a reading of the code alone.
