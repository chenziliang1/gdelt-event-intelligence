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

## The enhanced (Deep Dive) report

The second report button, `/analyze/event-report`, had no gate. It now goes through the same one
(`EnhancedReportGenerator.generate_event_report` calls `ReportGenerator._gate`), with two of the checks: numbers
must appear in what the model was given (the full input text: query results, storyline, GKG tone timeline, actor
activity) and counts of records must not exceed the records. Trends, totals and dates outside the query are not
checked here, because this report legitimately has a daily time series, complete daily totals and a storyline up
to a month around the query. The response says which checks ran (`checked`), and the panel lists exactly those.

Three live enhanced reports (2026-10-09; the query data fetched with the free local router first): all passed. The
first run of the third flagged "17 Dec (564 articles, 88 events)", a quote of the daily totals, as a count of
records, and asked for a rewrite (37 s instead of about 21 to 28 s). The count check now skips a count written in
digits that is itself a value in the data; every real miscount found so far was written as a word, and the 7 flags
on the 216 saved reports are unchanged. Re-run, the same report passed on the first draft (24 s).

## In the browser

Headless Chrome against the running app (`vite` and the API, 2026-10-09). Live: a Quick Report showed its pass line
under the report (with the first wording, which listed all checks for both reports and was then changed to list the
checks that ran), and after the change a Deep Dive Report showed "Passed the checks against the data: numbers and
record counts."; the definitions line is shown with the results. The two other states were shown with a mocked API
response, since neither occurred live: "Passed the checks after one rewrite (first draft had a
wrong count of records)." and, for a fallback, the record list under "The AI-written summary did not pass the
automatic checks (numbers not in the data), so the results are listed without interpretation." with an amber line
"The AI summary failed the checks twice ...".

## The fallback in real requests

With Claude Sonnet the fallback never occurred (0 of 216 saved reports, 0 of the live ones). To see it in real
requests, the report model was switched to the local `qwen2.5:3b` through the report API's own `llm_config`
(Ollama's OpenAI-compatible endpoint), same backend, same data, no code change (`weak_model_live.json`):

| qwen2.5:3b, saved questions | passed first | passed after a rewrite | fell back |
| :-- | --: | --: | --: |
| Quick Report, 54 | 52 | 2 | 0 |
| Deep Dive Report, 20 | 17 | 0 | **3** |

The three fallbacks were right: rebuilding the exact input each Deep Dive model call received, none of the flagged
numbers is in it. `brief-01` was given a 126-character input (three daily figures) and wrote "20,000", "5,978",
"3,416" and "2,892"; `overview-01` wrote "1,239,867" where the total is 931,529; `hot-02` wrote "637" three times.
Each rewrite repeated the same numbers, and the user got the records and totals from the data instead, under the
line naming the failed check. Whether the 3B model's passing reports contain errors the checks miss was not looked
at.

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
* With the production model the fallback has not occurred; it was seen in real requests only with a weak local
  model (above), and is also covered by unit tests with a scripted model.
* A rewrite adds one model call to the share of reports that fail, 8 of 216 here; live it added about 13 s to an
  enhanced report (37 s against 24 s for the same report passing first time).
* The gate does not check interpretation. The Deep Dive report's prompt has since been changed with the quick
  report's rules (judge: 108 unsupported sentences in 15 reports before, 1 after: `../2026-10-09_enhanced_prompt/`).
* The checks are as good as their recall, which is not measured (`../2026-10-09_report_numbers/summary.md`).
