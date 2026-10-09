# Report safety net: the checks run on every live report, 2026-10-09

Until now the six deterministic checks (`backend/services/report_checks.py`, formerly `tests/answer_quality.py`)
ran only in offline evaluations; a report shown in the app was never checked. Now `ReportGenerator.generate` runs
them on every report before it is returned:

1. The report passes: it is returned, with `checks: {passed: true, attempts: 1}`.
2. It fails: the model gets its own report back with a message that names each failing check and what it flagged
   (`rewrite_request`: "These sentences give a number of records larger than the listed records that match: ...
   Count the listed records again, or name them instead."), and the rewrite is checked again.
3. The rewrite fails too, or the call errors: the user gets a deterministic summary of the same records the model
   was given (no model involved), headed by which checks failed. The failing text is never shown.

The client sends the plan with the report request, so the date-window check applies; the response carries the check
result, and the report panel shows one line: passed, passed after one rewrite (and what the first draft had), or
fell back. Every answer also carries a definitions line built from the plan (`backend/agents/definitions.py`):
"Counts are GDELT event records, not articles; location is where the event took place (ActionGeo); protest = CAMEO
root code 14; event lists are the most-covered records, a sample, not totals; data covers 2024-01-01 to 2024-12-31;
relative dates are resolved as of 2024-12-31."

## How it would have handled the saved reports

`tests/eval_report_rewrite.py` re-checks all 216 saved reports (2026-10-08, 2026-10-09 and the two prompt changes)
and sends each failing one back once, as the live path does (`results.json`, 8 calls to `claude-sonnet-5-5`):

| | reports |
| :-- | --: |
| Passed on the first draft | 208 |
| Failed, rewritten once | 8 |
| Rewrite passed | **8** |
| Would have fallen back | 0 |

The rewrites fixed the error rather than hiding it: "four records, each with 70 articles" became "three records";
"six events" on January 18 became "five records ... four located in Uvalde and one at Robb Elementary School";
"three events ... 200 articles each" became "two events"; "five records are dated 2024-03-12" became "four", with the
four named; "Coverage rose toward the end of the month" was removed.

A live request through the running API (2026-10-09, `top protest events in Texas in April 2024`) returned the
definitions line with the plan and a report with `checks: {passed: true, attempts: 1, fallback: false}`.

## Limits

* The saved failing reports were written with older prompts; the rewrite used the current one. A rewrite fixes what
  was flagged and keeps the rest, so interpretation in an old draft stays (the checks do not cover it; the judge does
  offline).
* The fallback path did not occur on real reports; it is covered by unit tests with a scripted model
  (`tests/test_report_safety_net.py`: pass, rewrite, fallback, a rewrite that errors, no plan).
* A rewrite adds one model call (about the cost of the first) to the share of reports that fail, 8 of 216 here.
* The checks are as good as their recall, which is not measured (`../2026-10-09_report_numbers/summary.md`).
