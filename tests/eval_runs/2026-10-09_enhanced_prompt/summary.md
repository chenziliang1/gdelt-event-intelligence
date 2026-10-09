# Deep Dive (enhanced) report prompt: unsupported interpretation, before and after, 2026-10-09

The quick report's prompt was changed earlier (`../2026-10-09_report_prompt/`); the Deep Dive report
(`/analyze/event-report`, `ENHANCED_REPORT_SYSTEM_PROMPT` in `backend/agents/enhanced_reporter.py`) still asked
for "a narrative journalist" report with "broader implications". In the browser it read meaning into labels ("The
Palestine label ties the 24 April cluster to pro-Palestinian demonstrations"). Its prompt now carries the quick
report's rules, adapted to its extra inputs: article text, GKG themes and the daily actor figures count as data and
are attributed; the storyline is a selection by shared actors and dates, not a chain of causes; no explanations of
odd-looking records; sections end with "What the data cannot show" instead of implications.

## Set-up

15 of the 30 hand-labelled questions (seed 2026), the saved query data of `../2026-10-09_answer_quality/`, the
enrichments read live from MySQL inside the backend container (`tests/eval_enhanced_reports.py`), same model
(`claude-sonnet-5-5`), with the live gate (checks, one rewrite, fallback). Each row keeps the exact text the model
was given (`model_input`), and the judge (`tests/causal_judge.py`, recall 0.91 and precision 0.85 against reviewed
labels on quick reports) read that text, not only the query data. Only the prompt differs between `before.json` and
`after.json`.

A bug found on the way: the Deep Dive summary was cut at a fixed 4,000 characters while the prompt allowed 12,000,
so most reports ended mid-sentence with "..." (the first `before` run: 13 of 15 at exactly 4,003 characters). It is
now cut at the configured `max_report_length`, and `before.json` was generated again after the fix.

## Result

| 15 questions | old prompt | new prompt |
| :-- | --: | --: |
| Sentences the judge calls U (unsupported) | **108** of 993 | **1** of 913 |
| Reports with at least one U | 15 | 1 |
| Sentences the judge calls H (flagged guess) | 23 | 1 |
| Sentences the keyword rule lists | 123 | 15 |
| Passed the deterministic checks on the first draft | 12 (3 rewritten) | 15 |
| Mean length | 5,108 characters | 4,062 characters |

Typical U sentences before: "Its tone was mildly positive or neutral, which suggests procedural coverage rather
than violence."; "They most likely reflect GDELT's automated coding errors ..."; "Together they point to
congressional-executive engagement centered on the House Speaker."; "Ontario's event activity in November 2024 was
mostly routine." The one U after, "They share dates or actors only by selection.", states a limit of the storyline
rather than a guess; the judge's call is debatable. Every new report has the requested sections (Overview, Key
records, Actor activity, Media themes and tone, What the data cannot show; Timeline in 14).

## Limits

* None of the 15 inputs had a GKG section or a storyline section (the storyline is built around one primary event;
  GKG returned nothing in this environment), so the rules about them were not exercised. The inputs had the query
  records, the event context and, in 10 of 15, the daily actor activity.
* The prompt was written after reading the old reports of these questions in the browser and the judge's labels on
  the `before` group; there is no separate held-out set for the Deep Dive report.
* No person labelled these reports; the judge was validated on quick reports, not on this longer format.
* One generation per question per prompt.
