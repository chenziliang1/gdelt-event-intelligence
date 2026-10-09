# Held-out paraphrase set, 2026-10-08

`tests/agent_eval_heldout.json`: 29 new phrasings of the same intents, written after `agent_eval_set.json` reached
31/31 and before this set was ever run. Expectations are the intended behaviour, fixed in advance. Same live set-up
as `../2026-10-08_after_fixes/` (restored database, local Ollama router), with the remote LLM disabled (no API key
in the backend), and 0 remote calls in every run.

## Result

| | Passed |
| :--- | ---: |
| **First run, truly held out** (`agent_eval_results.json`) | **24 / 29** |
| After fixing the five failures (`after_fixes.json`; two identical runs) | 29 / 29 |
| Original set after the same fixes (no regressions; two runs) | 31 / 31 |

**24/29 is the number that measures generalisation.** The 29/29 is not held-out any more: the planner was changed
in response to these failures, so it only shows the failures were fixed. A new unseen set is needed for the next
honest measurement.

## The five failures and what they had in common

Four of the five were the 3B router's intent label disagreeing with the user's words, which the previous fixes had
only handled for the exact phrasings in the original set.

| Item | What happened | Fix (regression test in `tests/test_planner_offline.py`) |
| :--- | :--- | :--- |
| `h-compare-01` "more protests in Texas in October than in September?" | Router said `detail` (no ID in the text), and comparisons were skipped for `detail` | A `detail` label without an event ID is treated as search; real IDs never reach the router |
| `h-compare-02` "Did conflict events in Mexico go down this month?" | "go down" was not a comparison word | Comparison words now include go/went up/down, decline, fewer, spike, surge |
| `h-march-01` "news about the march on Washington DC" | Router said `brief`; the brief defaults to yesterday, so one day was searched | A `brief` label needs a briefing word (recap, summary, digest, what happened, ...) |
| `h-brief-01` "give me a recap of 2024-11-05" | Router said `search` on one run and `overview` (no place) on reruns | A briefing word plus a single day is a daily brief |
| `h-reldate-04` "conflict events in Mexico this week" | "This week" on Tuesday 2024-12-31 is Dec 30 to Jan 5; the range was accepted and queried into 2025 | Ranges must lie inside the dataset; relative ranges are cut at its end and the notice says so |

The router also gave different labels for the same sentence across runs (`h-brief-01`), which is why the fixes check
the user's words rather than trusting any one label.
