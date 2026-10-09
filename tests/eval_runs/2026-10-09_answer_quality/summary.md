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
at least one unsupported cause, motive or connection (75 of 447 sentences). A keyword rule finds 59% of them; a Claude
judge finds 91% with precision 0.84, so it is used as an offline evaluation, not as a pass/fail check.

Counts written as words ("four records") and event-type descriptions are not checked either; the review found one
of each wrong.
