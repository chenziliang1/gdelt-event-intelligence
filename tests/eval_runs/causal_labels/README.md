# Hand labels: causes and motives in reports

30 reports from `../2026-10-09_answer_quality/` (seed 2026, `tests/make_causal_labeling.py`), 447 sentences. These
labels are the reference for the causal rule (`tests/answer_quality.causal_candidates`) and the Claude judge
(`tests/causal_judge.py`). Labels were made before the judge was run on these reports.

## How to label

Read a report in `reports.md` (open "Data given to the report model" to see exactly what the model had), then fill
the `label` column of `labels.csv` for that report's sentences:

| label | meaning | examples |
| :-: | :-- | :-- |
| **U** | Unsupported: says or implies *why* something happened, what an event was really about, or that events are connected, and the data shown does not say so. Includes outside knowledge the data does not contain. A hedge ("likely", "suggests") does not make it supported. | "points to a judicial dispute involving the state"; "Robb Elementary was the site of the 2022 shooting, so ... suggests ongoing attention to that case" |
| **H** | Hedged and flagged as inference: the same kind of guess, but the sentence itself says the data does not establish it. | "may describe the same episode, but the data doesn't confirm that" |
| *(empty)* | Fine: restates the data, describes what the data is or cannot show, or explains the method. | "The data does not say what caused it."; "Because only five events are available, I can draw no firm conclusions." |

A title or summary in the data counts as data: "a protest over the ruling" is supported if the record's title says so.
Use `note` for anything unclear. When unsure between U and H, choose U if a reader could take the sentence as a finding.

## What is measured

* Per report: does it contain at least one U? (the unit the judge must get right)
* Per sentence: rule recall on U sentences (a U the rule does not list is a miss), judge precision and recall on U.
