"""
Human review of the pre-labels for causal and motive claims.

An independent Claude agent (Opus; it read only README.md and reports.md, not the rule or the
judge prompt) labelled all 447 sentences (prelabels.json). A person then reviews a short sheet
instead of labelling from scratch:

    python tests/make_causal_review.py --build   # review.csv: every U/H pre-label + 40 random "" sentences
    # edit the `final` column of review.csv (U, H or empty); leave it as is to agree
    python tests/make_causal_review.py --merge   # labels.csv = pre-labels, with the reviewed rows replaced

After the judge has run, a second, blind round covers the sentences the judge called U that the
person had not seen (pre-label empty, not sampled):

    python tests/make_causal_review.py --build-second   # review2.csv, `final` empty, judge label hidden
    python tests/make_causal_review.py --merge          # review2.csv overrides as well

The reference labels are therefore "pre-labelled by a model, reviewed by a person": sentences the
model left empty and the sample did not include were not seen by the reviewer, and the pre-label
may anchor the reviewer. review.csv keeps both columns so agreement can be reported.
"""

import argparse
import csv
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
LABELS = HERE / "eval_runs" / "causal_labels"
BLANK_SAMPLE, SEED = 40, 2026
FIELDS = ["report_id", "sentence_no", "sentence", "prelabel", "why", "final", "note"]


def rows():
    return list(csv.DictReader((LABELS / "labels.csv").open()))


def build():
    pre = json.loads((LABELS / "prelabels.json").read_text())["labels"]
    out, blanks = [], []
    for r in rows():
        p = pre[r["report_id"]][r["sentence_no"]]
        row = {"report_id": r["report_id"], "sentence_no": r["sentence_no"], "sentence": r["sentence"],
               "prelabel": p["label"], "why": p.get("why", ""), "final": p["label"], "note": ""}
        (out if p["label"] else blanks).append(row)
    sample = random.Random(SEED).sample(blanks, min(BLANK_SAMPLE, len(blanks)))
    keep = {(r["report_id"], r["sentence_no"]) for r in out + sample}
    ordered = [r for r in out + sample]
    ordered.sort(key=lambda r: (r["report_id"], int(r["sentence_no"])))
    with (LABELS / "review.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(ordered)
    print(f"review.csv: {len(out)} U/H pre-labels + {len(sample)} sampled empty = {len(keep)} rows")


def build_second():
    """review2.csv: sentences the judge marked U that the person never saw (pre-label "", not sampled).

    Blind: neither the judge's label nor a pre-filled answer is shown; `final` starts empty.
    """
    judged = json.loads((LABELS / "judge.json").read_text())["labels"]
    pre = json.loads((LABELS / "prelabels.json").read_text())["labels"]
    seen = {(r["report_id"], r["sentence_no"]) for r in csv.DictReader((LABELS / "review.csv").open())}
    out = [{"report_id": r["report_id"], "sentence_no": r["sentence_no"], "sentence": r["sentence"], "final": "", "note": ""}
           for r in rows()
           if judged[r["report_id"]][r["sentence_no"]] == "U" and not pre[r["report_id"]][r["sentence_no"]]["label"]
           and (r["report_id"], r["sentence_no"]) not in seen]
    with (LABELS / "review2.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["report_id", "sentence_no", "sentence", "final", "note"])
        w.writeheader()
        w.writerows(out)
    print(f"review2.csv: {len(out)} rows")


def merge():
    pre = json.loads((LABELS / "prelabels.json").read_text())["labels"]
    reviewed = {(r["report_id"], r["sentence_no"]): r for r in csv.DictReader((LABELS / "review.csv").open())}
    second = LABELS / "review2.csv"
    if second.exists():
        for r in csv.DictReader(second.open()):
            key = (r["report_id"], r["sentence_no"])
            first = reviewed.get(key)
            if first:
                # Only after the splitter fix (resplit.py): halves reviewed in different rounds
                # became one sentence; it is U if either answer was U, else H if either was H.
                finals = {first["final"].strip().upper(), r["final"].strip().upper()}
                r = {**r, "final": "U" if "U" in finals else "H" if "H" in finals else "",
                     "note": " / ".join(n for n in (first["note"], r["note"]) if n)}
            reviewed[key] = {**r, "prelabel": first["prelabel"] if first else ""}
    changed = 0
    all_rows = rows()
    for r in all_rows:
        key = (r["report_id"], r["sentence_no"])
        label = pre[r["report_id"]][r["sentence_no"]]["label"]
        if key in reviewed:
            final = reviewed[key]["final"].strip().upper()
            changed += final != reviewed[key]["prelabel"]
            label, r["note"] = final, reviewed[key]["note"]
        r["label"] = label
    with (LABELS / "labels.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["report_id", "sentence_no", "sentence", "label", "note"])
        w.writeheader()
        w.writerows(all_rows)
    if second.exists():
        finals = [r["final"].strip().upper() or "empty" for r in csv.DictReader(second.open())]
        print("second round:", {k: finals.count(k) for k in sorted(set(finals))})
    by = {}
    for r in csv.DictReader((LABELS / "review.csv").open()):
        k = r["prelabel"] or "empty"
        by.setdefault(k, [0, 0])
        by[k][0] += 1
        by[k][1] += r["final"].strip().upper() == r["prelabel"]
    print(f"labels.csv written; reviewer changed {changed} of {len(reviewed)} reviewed rows; first round "
          f"kept by pre-label: {', '.join(f'{k} {a}/{n}' for k, (n, a) in sorted(by.items()))}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="store_true")
    p.add_argument("--build-second", action="store_true")
    p.add_argument("--merge", action="store_true")
    a = p.parse_args()
    build() if a.build else build_second() if a.build_second else merge()
