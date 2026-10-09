"""
Build the hand-labelling set for causal and motive claims in reports.

    python tests/make_causal_labeling.py   # writes tests/eval_runs/causal_labels/{reports.md,labels.csv}

30 of the 54 saved reports of tests/eval_runs/2026-10-09_answer_quality/ (fixed seed). For each
report, reports.md shows the question, the exact data text the report model was given, and the
report split into numbered sentences; labels.csv has one row per sentence with an empty label.
Every sentence is listed, not only the rule's candidates, so the rule's recall can be measured.
"""

import csv
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from answer_quality import sentences  # noqa: E402
from backend.agents.planner import ReportGenerator  # noqa: E402

RUN = HERE / "eval_runs" / "2026-10-09_answer_quality" / "results.json"
OUT = HERE / "eval_runs" / "causal_labels"
N, SEED = 30, 2026


def main() -> None:
    results = [r for r in json.loads(RUN.read_text())["results"] if r.get("report")]
    chosen = sorted(random.Random(SEED).sample(sorted(results, key=lambda r: r["id"]), N), key=lambda r: r["id"])
    fmt = ReportGenerator.__new__(ReportGenerator)  # only the formatter is used, no LLM client
    OUT.mkdir(parents=True, exist_ok=True)
    md = ["# Reports to label", "",
          "Label in `labels.csv` (see `README.md`). Each report shows the data text the report model was given.", ""]
    rows = []
    for r in chosen:
        md += [f"## {r['id']}", "", f"**Question:** {r['query']}", "",
               "<details><summary>Data given to the report model</summary>", "", "```",
               fmt._format_data_for_report(r["data"]), "```", "", "</details>", "", "**Report:**", ""]
        for i, s in enumerate(sentences(r["report"]), 1):
            md.append(f"{i}. {s}")
            rows.append({"report_id": r["id"], "sentence_no": i, "sentence": s, "label": "", "note": ""})
        md.append("")
    (OUT / "reports.md").write_text("\n".join(md))
    with (OUT / "labels.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["report_id", "sentence_no", "sentence", "label", "note"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(chosen)} reports, {len(rows)} sentences -> {OUT}")


if __name__ == "__main__":
    main()
