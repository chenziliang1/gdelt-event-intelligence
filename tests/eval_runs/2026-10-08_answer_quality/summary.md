# Answer quality, 2026-10-08

Does the report the LLM writes (`/api/v1/analyze/report`, as the frontend calls it) stay faithful to the data it was
given? Questions: both eval sets minus greetings (55); one returned no data, so 54 reports. Model:
`claude-sonnet-5-5` via Anthropic's OpenAI-compatible endpoint. Checks: `tests/answer_quality.py` (deterministic,
no LLM judge). Raw reports, plans and data: `results.json`.

## Result

**54 / 54 reports pass all four checks.**

| Check | Failures |
| :--- | ---: |
| Every number in the report is in the data (1,315 numbers across the 54 reports) | 0 |
| No total or percentage trend stated from a top-N sample | 0 |
| Comparison reports state the computed direction | 0 (5 comparison reports) |
| No date outside the queried window | 0 |

43 of the 54 reports say explicitly that the events are a sample, and 37 say what the data cannot show.

## How the number was reached, including what went wrong

1. **Every report request failed with HTTP 500** on the first attempt. `ReportRequest` had no `llm_config` field
   and the route reads it, so `/analyze/report` raised `AttributeError` before calling any LLM. This was in the code
   as shipped (the frontend's "AI report" button could never have worked). Fixed in `backend/schemas/responses.py`,
   with a regression test. No LLM request was made by those failed calls.
2. Claude rejected `temperature` for this model; `build_llm` no longer sends it for Claude.
3. The first scored run was 52 / 54. Both failures were bugs in the checks, not in the reports: "December 23-29,
   2024" was read as the number 29, and "December 2024" as the date December 20. The checks were fixed (tests added)
   and the same 54 saved reports were re-scored without new LLM calls: 54 / 54.

LLM requests for this evaluation: 1 connectivity test (rejected: temperature), 1 connectivity test, 1 single-item
dry run, 54 reports. The planner's remote fallback was not triggered.

## What the checks do not catch

* **Qualitative trend claims without a number.** `month-end-03` (a top-50 sample of February events) says "Coverage
  rose toward the end of the month." That is a trend read off a sample, which the report prompt forbids; the check
  only flags trend words attached to a percentage, because the bare words also appear in legitimate sentences
  ("fell on that day", "can't say whether activity rose or fell"). Found by reading the reports, 1 of 54.
* **Interpretation beyond the data.** Reports infer causes ("consistent with labor-related action"); most hedge
  them ("the data does not say"), and none of these is checked automatically.
* The report formatter announces the full event count in its header but lists only 10 events; one report noticed
  and said so ("The data lists 10 events, though the header says 18").
