# Held-out paraphrase set v2, 2026-10-09

`tests/agent_eval_heldout_v2.json`: 28 new phrasings, written after every planner change prompted by v1 and before
this set was run. It adds Canadian and Mexican places, a comparison with a period outside the dataset, and phrasings
not used before. Live set-up as before (restored database, local Ollama router), remote LLM disabled, 0 remote calls.

## Result

| | Passed |
| :--- | ---: |
| **First run, truly held out** (`agent_eval_results.json`) | **23 / 28** |
| After fixing the failures (`after_fixes.json`) | 28 / 28 |
| Original set and v1 after the same fixes | 31 / 31, 29 / 29 |

The first attempt was voided by infrastructure, not routing: MySQL was killed for lack of memory (Docker, 4 GB buffer
pool) after 5 items, and 23 requests failed to connect. The run above is the first one in which every item reached
the planner. Its 23/28 is the generalisation number, close to v1's 24/29.

## The five failures

| Item | What happened | Fix |
| :--- | :--- | :--- |
| `v2-detail-02` "explain CA-20240603-CRO-APPEAL-1179504660" | HTTP 400: the detail plan parsed any non-`US-`, non-`EVT-` ID with `int()` | The similar-events seed is the trailing GlobalEventID of any fingerprint |
| `v2-hot-02` "biggest news on 2024-07-04" | Router said brief, downgraded to search (no briefing word); "biggest" was not a hot-events word | Hot words checked after the label checks, including "biggest / major news" |
| `v2-overview-01` "overview of British Columbia this year", `v2-overview-02` "how is Jalisco doing in Q3" | Router said overview but dropped the place, and the place was only recovered for search/brief labels | Recover the place for an overview label without one; the generated place list has Canadian and Mexican divisions |
| `v2-invalid-01` "events in Texas on 2024-06-31" | Timed out (30 s): the full-year Texas search scanned every row with `LIKE` | Indexed `ActionGeo_ADM1` column (12.7 s to 0.3 s), see `docs/DATA_LAYER.md` |

## Routing confidence was not calibrated

Each plan now carries an evidence-based routing confidence (`Planner._routing_confidence`): low when the router's
label was overridden or the date was unusable, medium when only the dates were re-derived, high otherwise. On the
first v2 run: high 11/13 passed, medium 2/2, low 7/8. Low-confidence items were not more often wrong, because "low"
marks exactly the cases the rules caught and corrected; the failures were router errors the rules did not detect,
labelled high. The field is kept for transparency (it shows which rule fired), not as a predictor of errors.
