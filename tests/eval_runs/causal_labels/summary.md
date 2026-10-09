# Causes and motives in reports: rule vs Claude judge, 2026-10-09

30 of the 54 reports from `../2026-10-09_answer_quality/` (447 sentences), labelled U / H / empty as defined in
`README.md`. The rule is `tests/answer_quality.causal_candidates`; the judge is `claude-sonnet-5-5` with the prompt
in `tests/causal_judge.py`, one call per report, run once (`judge.json`). Scores: `scores.json` (final labels),
`scores_round1.json` (before the second review round).

## How the labels changed

| step | U | H | reports with a U |
| :-- | --: | --: | --: |
| Pre-labels (Opus agent) | 64 | 8 | |
| After review round 1 (all 72 U/H pre-labels + 40 sampled empty) | 63 | 6 | 24 / 30 |
| After review round 2 (the 23 judge-U sentences the reviewer had not seen; blind) | **75** | **6** | **25 / 30** |

Round 1: the reviewer changed 4 of 112 rows (U kept 62/64, H 6/8, sampled empty 40/40).
Round 2: 12 of the 23 sentences were U. So the pre-labeller missed U sentences that the 40-sentence sample did not
reveal.

## Result (final labels)

| | sentences: precision | recall | flagged | reports: flagged | missed |
| :-- | --: | --: | --: | --: | --: |
| Rule | 0.51 (44/87) | 0.59 (44/75) | 87 | 29 / 30 | 0 |
| **Judge** | **0.84 (68/81)** | **0.91 (68/75)** | 81 | 28 / 30 | 0 |
| Rule and judge | 0.87 (39/45) | 0.52 (39/75) | 45 | 23 / 30 | 4 |

Before round 2 the judge's precision was 0.69 (0.97 on the rows the reviewer had seen) and recall 0.89; the rule's
0.43 and 0.59.

What this says:

* **The report model often goes beyond the data.** 25 of 30 reports contain at least one unsupported cause, motive
  or connection (75 of 447 sentences, 17%). Typical: an actor label read as a topic ("STUDENT" → "campus activism",
  "FIREFIGHTER" → the "Fighting" code means firefighting), events joined into one story ("a connected exchange"),
  or a summary sentence naming the month's theme.
* **The rule is not a usable detector.** It misses 31 of 75 U sentences, because many are phrased as plain
  statements with no causal word ("The armed violence centers on cartel activity."), and half of what it flags is
  fine. Combining it with the judge only loses recall.
* **The judge is usable for evaluation, not as a gate on single reports.** Per sentence it finds 9 in 10 and is
  right 5 times in 6. Per report it cannot separate good from bad here, because almost every report has a U: it
  flagged 28 of 30, including 3 of the 5 reports without one.
* 4 of its 7 misses claim something and then add a limit ("suggests a court ruling ..., though the records don't
  name the case"), and it labelled them H. That is the boundary the label definition calls hardest, and where the
  reviewer also changed labels in both directions. The other 3 imply a cause without a causal word ("in Eagle Pass,
  a border community").

Follow-up: the report prompt was changed and the reports regenerated; the judge finds 0 U sentences in the
same 30 (`../2026-10-09_report_prompt/summary.md`).

## Limits

* The 312 sentences that the pre-labeller left empty, the judge did not call U, and the sample did not include were
  never seen by a person. 0 of 40 sampled empty sentences were U, but round 2 showed U sentences exist in that pool, so the
  judge's recall is an upper bound.
* Round 2 reviewed only sentences the judge called U, which favours the judge: its false positives were checked,
  sentences it and the pre-labeller both missed were not. It was blind (no judge label, no pre-filled answer).
* The pre-labeller (Opus) and the judge (Sonnet) are both Claude, with the same definitions. One run of the judge;
  its variance is not measured. 30 reports from one report model.
* `answer_quality.sentences` splits after "U.S.", so `search-02` sentences 1 and 2 are halves of one sentence; the
  labels keep that split.

## Found while reviewing, not causal

The reviewer's notes also found two factual slips the number check cannot catch, because the count is a word and
the type is a description: `detail-01` says "four records" at Perry High School where the data has three, and
`h-reldate-04` says the other nine events are all fighting where one is an assault.

## Reproduce

```bash
python tests/make_causal_review.py --merge                       # labels.csv from prelabels.json + review.csv + review2.csv
python tests/causal_judge.py --score tests/eval_runs/causal_labels/judge.json   # no API calls
python tests/causal_judge.py --out judge.json                    # 30 calls to the judge, ANTHROPIC_API_KEY
```
