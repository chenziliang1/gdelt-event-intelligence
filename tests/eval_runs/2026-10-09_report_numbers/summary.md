# Report prompt: number rules, and rounded bounds in the number check, 2026-10-09

After the interpretation rules (`../2026-10-09_report_prompt/`), the remaining check failures were about numbers:
rounded totals ("more than 20,000" for 20,942) and miscounted records ("two records ... at 90 articles each" where
one had 90). Both sides were changed:

* **Check** (`tests/answer_quality.py`): a number stated as a bound ("more than", "over", "at least", "-plus",
  "or more"; "under", "fewer than", "at most") is grounded when a data value is on the stated side and within 10%.
  It is judged only as a bound, so "more than 21,000" for 20,942 fails although the two are within rounding. "Over
  31 days" is a span, not a bound. A bare rounded number still needs to be within 0.5%.
* **Count check**: compares with the records the report model was shown (the formatter shows 8 related events, 10
  listed events, 5 hot events), reads "five of the ten records", date ranges ("between X and Y"), and uses an
  article count only when it applies to each record ("each with 70 articles", not "each had 120 articles, except
  the third"); dates and counts inside parentheses describe one item and are left out. Each of these was a false
  flag found by reading every flag on the saved reports.
* **Prompt** (`REPORT_SYSTEM_PROMPT`): write every number exactly as in the data; before writing "N records", count
  the listed records that match, and name them instead if unsure.

## Result

The 54 reports were regenerated from the same saved data (`tests/rerun_reports.py`, `results.json`), so only the
prompt changed. All runs are scored with the same, final checks (no new LLM calls for the older runs:
`../2026-10-09_answer_quality/recheck_count_and_split.json`, `../2026-10-09_report_prompt/recheck_count_and_split.json`).

| same 54 questions | old prompt | + interpretation rules | + number rules |
| :-- | --: | --: | --: |
| Pass all six checks | 51 | 52 | **53** |
| Wrong record counts (`count_overclaims`) | 3 | 2 | 1 |
| Rounded totals flagged | 0 | 0 (2 before bounds were accepted) | 0 |
| Judge: unsupported sentences, 30 labelled questions | 80 | 0 | **0** |
| Judge: unsupported sentences, other 24 | 26 | 0 | **0** |
| Words, all 54 | 13,915 | 11,937 | 11,877 |

The one failure, `march-word-01`, contradicts itself: "In total, five of the ten records are dated 2024-04-24
(... plus none other), so that date is shared by four listed records". Four are; both the count check and the
"In total" check flag it. The new prompt did not stop miscounting; it made it rarer in this run (3, 2, 1 reports),
which one generation per question cannot separate from chance.

Rounded totals disappeared from the reports: the only number stated as a bound in the new reports is a threshold
taken from the data ("Several other records have 60 or more articles", with records at exactly 60).

The judge ran on all 54 new reports (`judge.json`, `judge_other24.json`, 54 calls): no U; 2 H sentences, both saying
two records may be duplicates and that the data does not say.

## Count check on every saved report

Across the 216 saved reports (2026-10-08, 2026-10-09, and the two prompt changes), the final count check flags 7
sentences; each was compared with the data and all 7 are wrong ("six of the ten events" on January 18 where five
are, "four records, each with 70 articles" where three are, ...). Precision on these is 7/7; recall is not measured,
since nobody counted every count sentence by hand.

## Limits

* The count check was narrowed on the same saved reports it is reported on (every false flag found led to a
  change, each covered by a unit test). On new reports it may flag correct sentences or miss wrong ones.
* It reads only dates and per-record article counts; counts described by place, actor or type are not checked.
* One generation per question; the drop from 3 to 1 wrong counts is not a measured effect.

## Reproduce

```bash
python tests/rerun_reports.py --run tests/eval_runs/2026-10-09_answer_quality/results.json \
    --out tests/eval_runs/2026-10-09_report_numbers/results.json                         # 54 report calls
python tests/causal_judge.py --run tests/eval_runs/2026-10-09_report_numbers/results.json \
    --out tests/eval_runs/2026-10-09_report_numbers/judge.json                           # 30 judge calls
python tests/causal_judge.py --unlabelled --run tests/eval_runs/2026-10-09_report_numbers/results.json \
    --out tests/eval_runs/2026-10-09_report_numbers/judge_other24.json                   # 24 judge calls
python tests/rerun_reports.py --checks-only --run <any results.json> --out <recheck.json>  # no calls
```
