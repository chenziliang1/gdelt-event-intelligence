"""
Claude judge for causes and motives in reports, measured against hand labels.

One call per report: the judge gets the exact data text the report model was given and the report
split into numbered sentences, and labels each sentence U (unsupported cause, motive or connection),
H (the same, flagged as inference in the sentence) or "" (fine), with the definitions the hand
labeller used (tests/eval_runs/causal_labels/README.md).

    python tests/causal_judge.py --dry-run                # print one prompt, no API call
    python tests/causal_judge.py --out tests/eval_runs/causal_labels/judge.json   # 30 calls, ANTHROPIC_API_KEY
    python tests/causal_judge.py --score tests/eval_runs/causal_labels/judge.json # compare with labels.csv, no calls

With --run, the judge labels the same 30 questions in another run (e.g. reports regenerated with a
new prompt by tests/rerun_reports.py); those reports have no hand labels, so --score does not apply.
With --unlabelled, it labels the other reports of the run instead (the ones not in labels.csv).

Scored three ways against the hand labels: the rule alone (answer_quality.causal_candidates), the
judge alone, and rule + judge (U only if the rule lists the sentence and the judge says U).
"""

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from answer_quality import causal_candidates, sentences  # noqa: E402

LABELS = HERE / "eval_runs" / "causal_labels"
RUN = HERE / "eval_runs" / "2026-10-09_answer_quality" / "results.json"

JUDGE_PROMPT = """You check reports written from GDELT event records for unsupported claims about causes, motives or connections.

The records contain only: date, place, actor labels, an event type code, a tone score, article counts, and sometimes a title or summary. They never say why something happened unless a title or summary says so.

Label every numbered sentence of the report:
- "U": the sentence says or implies why something happened, what an event was really about, or that events are connected, and the data shown does not say so. Outside knowledge the data does not contain counts. A hedge ("likely", "suggests", "points to") does not make it supported.
- "H": the same kind of inference, but the sentence itself says the data does not establish it ("may be the same episode, but the data doesn't confirm that").
- "": fine: restates the data, describes what the data is or cannot show, or explains the method ("Because only five events are available, I can draw no firm conclusions").
A title or summary in the data counts as data. If unsure between U and H, choose U when a reader could take the sentence as a finding.

Reply with JSON only: {"labels": {"1": "", "2": "U", ...}} with every sentence number."""


def report_inputs(run=RUN, unlabelled=False):
    from backend.agents.planner import ReportGenerator
    fmt = ReportGenerator.__new__(ReportGenerator)
    ids = {row["report_id"] for row in csv.DictReader((LABELS / "labels.csv").open())}
    for r in json.loads(Path(run).read_text())["results"]:
        if (r["id"] in ids) != unlabelled and r.get("report"):
            # A Deep Dive report was given more than the query data (tests/eval_enhanced_reports.py).
            yield r["id"], r.get("model_input") or fmt._format_data_for_report(r["data"]), sentences(r["report"])


def user_message(data_text, sents):
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sents, 1))
    return f"DATA GIVEN TO THE REPORT WRITER:\n{data_text}\n\nREPORT:\n{numbered}"


async def judge(out_path, model, run=RUN, unlabelled=False):
    from dotenv import load_dotenv
    from langchain_core.messages import HumanMessage, SystemMessage
    from backend.agents.planner import _extract_json, build_llm
    load_dotenv(HERE.parent / ".env")
    llm = build_llm({"provider": "claude", "model": model})
    results = {}
    for rid, data_text, sents in report_inputs(run, unlabelled):
        resp = await llm.ainvoke([SystemMessage(content=JUDGE_PROMPT), HumanMessage(content=user_message(data_text, sents))])
        raw = _extract_json(resp.content) or {}
        got = raw.get("labels") or {}
        results[rid] = {str(i): got.get(str(i), "") for i in range(1, len(sents) + 1)}
        missing = len(sents) - sum(str(i) in got for i in range(1, len(sents) + 1))
        print(rid, sum(v == "U" for v in results[rid].values()), "U", f"MISSING {missing}" if missing else "")
    Path(out_path).write_text(json.dumps({"model": model, "run": str(run), "unlabelled": unlabelled, "prompt": JUDGE_PROMPT, "labels": results}, indent=1))


def prf(pred, gold):
    tp = len(pred & gold)
    return {"tp": tp, "fp": len(pred - gold), "fn": len(gold - pred),
            "precision": round(tp / len(pred), 3) if pred else None, "recall": round(tp / len(gold), 3) if gold else None}


def score(judge_path):
    rows = list(csv.DictReader((LABELS / "labels.csv").open()))
    if not any(r["label"].strip() for r in rows):
        sys.exit("labels.csv has no labels yet")
    gold = {(r["report_id"], r["sentence_no"]) for r in rows if r["label"].strip().upper() == "U"}
    text = {(r["report_id"], r["sentence_no"]): r["sentence"] for r in rows}
    by_report = {}
    for (rid, no), s in text.items():
        by_report.setdefault(rid, []).append((no, s))
    rule = set()
    for rid, items in by_report.items():
        cands = set(causal_candidates("\n".join(s for _, s in sorted(items, key=lambda t: int(t[0])))))
        rule |= {(rid, no) for no, s in items if s in cands}
    judged = json.loads(Path(judge_path).read_text())["labels"]
    judge_u = {(rid, no) for rid, labels in judged.items() for no, v in labels.items() if v == "U"}
    reports = sorted(by_report)

    def report_level(pred):
        p = {rid for rid, _ in pred}
        g = {rid for rid, _ in gold}
        return {"reports_with_U": len(g), "agree": sum((rid in p) == (rid in g) for rid in reports), "of": len(reports), **prf(p, g)}

    out = {name: {"sentences": prf(pred, gold), "reports": report_level(pred)}
           for name, pred in (("rule", rule), ("judge", judge_u), ("rule_and_judge", rule & judge_u))}
    print(json.dumps(out, indent=1))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")
    p.add_argument("--score")
    p.add_argument("--run", default=str(RUN))
    p.add_argument("--unlabelled", action="store_true")
    p.add_argument("--model", default=os.getenv("JUDGE_MODEL", "claude-sonnet-5-5"))
    a = p.parse_args()
    if a.dry_run:
        rid, data_text, sents = next(report_inputs())
        print(JUDGE_PROMPT, "\n\n----\n", user_message(data_text, sents))
    elif a.score:
        score(a.score)
    elif a.out:
        asyncio.run(judge(a.out, a.model, a.run, a.unlabelled))


if __name__ == "__main__":
    main()
