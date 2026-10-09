# Answer quality, 2026-10-09 (after the trend-claim fix)

Same set-up as `../2026-10-08_answer_quality/` (both eval sets minus greetings, 54 reports from
`claude-sonnet-5-5`, deterministic checks in `tests/answer_quality.py`), after two changes made in response to that
run's one known miss:

* The report prompt and the event-list header now forbid describing change over time from a top-N sample, with or
  without numbers; the header also states how many of the matching events are listed (it said "TOP 18" above 10).
* A fifth check, `qualitative_trend`, flags such sentences unless hedged or negated. On the 54 saved reports of
  2026-10-08 it flagged exactly the one sentence found by reading them ("Coverage rose toward the end of the month."),
  and nothing else.

## Result

**54 / 54 reports pass all five checks** (1,305 numbers, all traceable to the data). `month-end-03`, the report that
had the trend claim, now says: "The sample is only ten events, so it shows what drew the most attention, not the full
scope of the month."

LLM requests: 54 reports, plus 1 report from the browser check of the Quick Report button, 55 in all, counted in the backend log. The planner's remote fallback was not triggered.

## Still not checked automatically

Interpretation beyond the data (causes, motives, connections) is not checked by the five checks above. It was
measured separately on 30 of these reports against reviewed labels (`../causal_labels/summary.md`): 25 of 30 contain
at least one unsupported cause, motive or connection (75 of 443 sentences). A keyword rule finds 60% of them; a Claude
judge finds 91% with precision 0.85, so it is used as an offline evaluation, not as a pass/fail check. After a
prompt change, the regenerated reports have none by the judge (`../2026-10-09_report_prompt/summary.md`).

Event-type descriptions ("the rest are fighting" when one is an assault) are not checked either.

## Recheck with a sixth check (count over-claims), same reports

The causal review found "four records" where the data has three; the number check ignores counts written as words.
`count_overclaims` (tests/answer_quality.py) takes sentences that say "N records/events" and name a date or a
per-record article count, and flags them when fewer than N of the records shown to the report model have them. It
only flags over-claims, since a sentence may describe a subset by something the rule does not read (details:
`../2026-10-09_report_numbers/summary.md`). On the saved reports, without new LLM calls
(`python tests/rerun_reports.py --checks-only ...`, `recheck_count_and_split.json`):

**51 of 54 pass all six checks.** The three failures are real: `detail-01` "four records, each with 70 articles" on
2024-01-05 at Perry High School (three), `confidence-probe-01` "six of the ten events" on January 18 in Uvalde (five),
and `invalid-date-01` "three events ... 200 articles each" on 2024-11-11 (two, and the sentence goes on to list two).
Across all 216 saved reports the final check flags 7 sentences, all wrong when compared with the data; earlier
versions flagged correct sentences (using places and actors, reading the day of a date as a count, applying a
count in parentheses or "for the first three" to every record) and were narrowed.

The same change fixed the sentence splitter, which broke after "U.S." and "vs." (`../causal_labels/resplit.py`).
