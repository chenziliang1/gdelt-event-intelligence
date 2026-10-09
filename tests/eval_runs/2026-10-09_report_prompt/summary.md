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

## Out of sample: the other 24 reports

The prompt was written after reading the failures in the 30 labelled reports. The other 24 reports of the run had
not been read, so the judge labelled them before and after (`judge_other24_before.json`, `judge_other24_after.json`,
48 calls, `causal_judge.py --unlabelled`):

| other 24 questions | before | after |
| :-- | --: | --: |
| Sentences the judge calls U | 26 | **0** |
| Reports with at least one U (judge) | 15 | **0** |
| Sentences the judge calls H | 12 | 1 |
| Sentences the keyword rule lists | 39 | 6 |
| Words | 5,771 | 5,121 |

The 6 rule sentences are again statements of limits or method ("the per-day figures reflect the same window
length"); the H sentence says two records may be the same occurrence and that it cannot tell. The before figures
are lower than in the 30 because these 24 include the period comparisons, which had little to interpret.

## Limits

* The 30 are in-sample (the prompt was written from their failures); the 24 are not, and show the same result.
* The judge was validated on reports in the old style. On the new reports nothing was labelled by a person; the
  rule's agreement is the only second signal. With recall at most 0.91, "0" means few, not none.
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
python tests/causal_judge.py --unlabelled --out tests/eval_runs/2026-10-09_report_prompt/judge_other24_before.json
python tests/causal_judge.py --unlabelled --run tests/eval_runs/2026-10-09_report_prompt/results.json \
    --out tests/eval_runs/2026-10-09_report_prompt/judge_other24_after.json       # 24 + 24 judge calls
```
