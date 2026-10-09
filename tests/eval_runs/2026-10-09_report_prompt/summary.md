# Report prompt change: unsupported interpretation, before and after, 2026-10-09

`../causal_labels/summary.md` found that 25 of 30 reports contained at least one cause, motive or connection the
data did not support. The report prompt asked for "a coherent story" with "implications or connections". It now
asks for a summary of what the records show, with explicit rules (`REPORT_SYSTEM_PROMPT` in
`backend/agents/planner.py`):

* actor labels, places, dates and tone do not say what an event was about or why it happened; only a title or
  summary that says so in words counts;
* no outside knowledge, no reinterpreting event type codes;
* no connecting records, no theme for the period; group only by shared fields and say so;
* a hedge does not make a guess acceptable.

## Set-up

Only the prompt changed. `tests/rerun_reports.py` regenerated all 54 reports of `../2026-10-09_answer_quality/`
from the saved plan and data (same `claude-sonnet-5-5`, no planner or database calls). The judge from
`../causal_labels/` (recall 0.91, precision 0.84 against reviewed labels) then labelled the reports for the same 30
questions (`judge.json`).

## Result

| 30 questions | before | after |
| :-- | --: | --: |
| Sentences the judge calls U (unsupported) | 81 | **0** |
| Reports with at least one U (judge) | 28 | **0** |
| Sentences the judge calls H (guess, flagged as such) | 13 | 2 |
| Sentences the keyword rule lists | 87 | 14 |
| Words | 8,144 | 6,816 |

All 14 sentences the rule lists are statements of what the data cannot show ("they do not show what any event was
about, what caused it, or who was involved"), so the rule agrees with the judge. The 2 H sentences say two records
may be duplicates and that the data does not say.

**Deterministic checks: 52 of 54 pass** (54 of 54 before). Both failures are rounded totals: "the other 63,000-plus
events" (data: 63,370) and "five records out of more than 20,000" (data: 20,942). They are true, but the number
check requires a value within 0.5% of the data, so they count as failures; the check was not loosened.

## Limits

* In-sample: the prompt was written after reading the failures in these 30 reports. The other 24 reports were not
  judged, before or after; judging them both ways (48 calls) would be the out-of-sample check.
* The judge was validated on reports in the old style. On the new reports nothing was labelled by a person; the
  rule's agreement is the only second signal.
* One generation per question, so variance is not measured; the drop from 81 to 0 is far larger than resampling
  would explain.
* The reports are drier: they list records and say what the data cannot show, instead of telling a story.
  Whether analysts find them less useful is not measured.

## Reproduce

```bash
python tests/rerun_reports.py --run tests/eval_runs/2026-10-09_answer_quality/results.json \
    --out tests/eval_runs/2026-10-09_report_prompt/results.json                  # 54 report calls
python tests/causal_judge.py --run tests/eval_runs/2026-10-09_report_prompt/results.json \
    --out tests/eval_runs/2026-10-09_report_prompt/judge.json                     # 30 judge calls
```
