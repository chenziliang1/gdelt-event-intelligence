"""
One-time migration (2026-10-09): renumber the sentence labels after the sentence splitter fix.

The old splitter (r"(?<=[.!?])\\s+|\\n+") broke after "U.S." and "vs.", so a sentence such as
"... mostly tied to U.S. political and legal disputes ..." was labelled as two halves. With the fix
in tests/answer_quality.sentences, each new sentence is one or more consecutive old ones. Labels of
merged halves are combined: U if either half is U, else H if either is H, else empty; notes and
reasons are joined. Applies to every file keyed by sentence number; idempotent.

    python tests/eval_runs/causal_labels/resplit.py
"""

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
TESTS = HERE.parent.parent
sys.path.insert(0, str(TESTS))
from answer_quality import sentences  # noqa: E402

OLD_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
RUNS = {"old": TESTS / "eval_runs" / "2026-10-09_answer_quality" / "results.json",
        "new": TESTS / "eval_runs" / "2026-10-09_report_prompt" / "results.json"}
PROMPT = TESTS / "eval_runs" / "2026-10-09_report_prompt"


def old_sentences(text):
    return [s.strip() for s in OLD_SPLIT.split(text or "") if s.strip()]


def groups(text):
    """For each new sentence, the 1-based numbers of the old sentences it is made of."""
    old, out, i = old_sentences(text), [], 0
    for new in sentences(text):
        g, acc = [i + 1], old[i]
        i += 1
        while " ".join(acc.split()) != " ".join(new.split()):
            acc += " " + old[i]
            i += 1
            g.append(i)
        out.append(g)
    assert i == len(old)
    return out


def combine(labels):
    return "U" if "U" in labels else "H" if "H" in labels else ""


def join(texts):
    return " / ".join(t for t in texts if t)


def reports(run):
    return {r["id"]: r["report"] for r in json.loads(RUNS[run].read_text())["results"] if r.get("report")}


def remap_judge(path, run):
    doc = json.loads(path.read_text())
    text = reports(run)
    for rid, labels in doc["labels"].items():
        g = groups(text[rid])
        if len(labels) == len(g):
            continue  # already migrated
        doc["labels"][rid] = {str(j): combine([labels[str(o)] for o in olds]) for j, olds in enumerate(g, 1)}
    path.write_text(json.dumps(doc, indent=1))


def remap_csv(path, merge_row):
    rows = list(csv.DictReader(path.open()))
    fields = list(rows[0].keys())
    text = reports("old")
    by = {}
    for r in rows:
        by.setdefault(r["report_id"], {})[int(r["sentence_no"])] = r
    out = []
    for rid in dict.fromkeys(r["report_id"] for r in rows):
        g = groups(text[rid])
        new = sentences(text[rid])
        for j, olds in enumerate(g, 1):
            parts = [by[rid][o] for o in olds if o in by[rid]]
            if not parts:
                continue
            row = merge_row(parts, olds) if len(olds) > 1 else dict(parts[0])
            row.update(sentence_no=str(j), sentence=new[j - 1])
            out.append(row)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)


def main():
    pre_path = HERE / "prelabels.json"
    pre_doc = json.loads(pre_path.read_text())
    pre_old = json.loads(json.dumps(pre_doc["labels"]))
    text = reports("old")
    if all(len(v) == len(sentences(text[rid])) for rid, v in pre_doc["labels"].items()):
        print("already migrated")
        return
    for rid, labels in pre_doc["labels"].items():
        g = groups(text[rid])
        pre_doc["labels"][rid] = {
            str(j): {"label": combine([labels[str(o)]["label"] for o in olds]),
                     "why": join([labels[str(o)].get("why", "") for o in olds])}
            for j, olds in enumerate(g, 1)}
    pre_path.write_text(json.dumps(pre_doc, indent=1))

    remap_csv(HERE / "labels.csv", lambda parts, olds: {
        **parts[0], "label": combine([p["label"] for p in parts]), "note": join([p["note"] for p in parts])})

    def review_row(parts, olds):
        # A reviewed half keeps the reviewer's answer; an unreviewed half contributes its pre-label.
        rid = parts[0]["report_id"]
        reviewed = {int(p["sentence_no"]): p for p in parts}
        finals = [reviewed[o]["final"] if o in reviewed else pre_old[rid][str(o)]["label"] for o in olds]
        return {**parts[0], "prelabel": combine([pre_old[rid][str(o)]["label"] for o in olds]),
                "why": join([pre_old[rid][str(o)].get("why", "") for o in olds]),
                "final": combine([f.strip().upper() for f in finals]), "note": join([p["note"] for p in parts])}
    remap_csv(HERE / "review.csv", review_row)
    remap_csv(HERE / "review2.csv", lambda parts, olds: {
        **parts[0], "final": combine([p["final"].strip().upper() for p in parts]), "note": join([p["note"] for p in parts])})

    remap_judge(HERE / "judge.json", "old")
    remap_judge(PROMPT / "judge.json", "new")
    remap_judge(PROMPT / "judge_other24_before.json", "old")
    remap_judge(PROMPT / "judge_other24_after.json", "new")
    print("migrated")


if __name__ == "__main__":
    main()
