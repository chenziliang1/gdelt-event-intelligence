# Router change and held-out v3, 2026-10-09

The planner's router (the model that turns a question into place, dates, event type and intent) was the local
qwen2.5:3b. It is now Claude Sonnet 5.5 (`ClaudeRouter`), with qwen as the fallback when there is no key or the call
fails. The deterministic checks behind the router (dates from the text, labels checked against the user's words)
are unchanged and apply to both.

Scoring is plan-only (`tests/run_planner_eval.py`): every expectation in the sets is about the plan, so no database
is needed. Sonnet's outputs were recorded once (`tests/fixtures/router_cassette_claude.json`, 118 questions) and
replayed; qwen ran live. The remote planner fallback was disabled in all runs (an item needing it fails).

## Held-out v3

30 questions written blind on 2026-10-09 by a separate Claude agent with no access to the code, the repository or
the earlier sets (`tests/agent_eval_heldout_v3.json`). Its date arithmetic was checked and nothing needed changing.

**First run, planner frozen** (`*_v3_first_run.json`):

| Router + rules | v3 |
| :-- | --: |
| Sonnet 5.5 | 23 / 30 |
| qwen2.5:3b | 21 / 30 |

Sonnet's seven failures, read one by one:

* 5 were the deterministic date layer overriding Sonnet's correct dates: "Dec 3rd" and "July 4" (no year) became
  the whole month, "between April and June" became April, "February 30" (no year) became February with no
  notice, and two explicit ISO ranges compared with each other became a one-day search. Sonnet had the right
  dates in all five.
* 2 were the scorer: single-day tools carry `query_date`, which the scorer did not read; the right day was planned.

The router itself made no v3 mistake that reached the plan. qwen made two of its own (below).

**After fixing the date parser and the scorer** (`*_all_after_fixes.json`; v3 is no longer held out from here):

| Router + rules | v1 (31) | v1 held-out (29) | v2 (28) | v3 (30) |
| :-- | --: | --: | --: | --: |
| Sonnet 5.5 | 31 | 29 | 28 | 30 |
| qwen2.5:3b | 31 | 29 | 28 | 28 |

qwen's remaining v3 failures: "Show me diplomatic meetings involving Canada and Mexico" labelled overview, and "a
general overview of British Columbia" planned with no dates.

## How much the rules had to repair

On the 74 dev questions that reach the router (v1, v1 held-out, v2), the rules overrode the router's intent label
22 times for qwen and 0 times for Sonnet; dates were re-derived from the text 13 times for qwen and 9 times for
Sonnet. With qwen the pass rate on the dev sets is the rules' work; with Sonnet the router is right on its own.

## Routing confidence

On the dev sets every item passed for both routers, so the levels could not be checked against failures there. One
change was made from the dev data before v3 was scored: an impossible date in the question no longer makes routing
"low" (all 5 such dev questions were planned correctly by both routers; the plan already carries a notice).

On the v3 first run (Sonnet), failures by level: high 2 of 14 (both the scorer gap), medium 5 of 9, low 0 of 2.
"Medium" means the dates were re-derived from the text, and that is where the date-parser bugs were: the level
pointed at the real problem. After the fixes there are no failures to calibrate against.

## Cost

131 Sonnet calls: 118 for the recording, 1 to inspect a failure, 12 to re-record. Those 12 had returned no text:
with `max_tokens=300`, Sonnet 5.5 spent the budget reasoning about impossible dates and "this week" before writing
the JSON (`finish_reason=length`); the limit is now 2048.
