"""
Deep Dive (enhanced) reports for a fixed sample of the saved questions, with the exact model input.

The enhanced report is built from the query results plus enrichments read from MySQL (storyline,
daily actor activity) and GKG, so it runs where the backend runs (inside the backend container):

    docker exec gdelt_backend python tests/eval_enhanced_reports.py \
        --out tests/eval_runs/2026-10-09_enhanced_prompt/before.json

One report call per question (plus a rewrite when the checks fail). Each row keeps the report, the
checks and `model_input`, the text the model was given, which tests/causal_judge.py --run uses in
place of the quick report's data text. The sample is 15 of the 30 hand-labelled questions (seed 2026).
"""

import argparse
import asyncio
import csv
import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

RUN = HERE / "eval_runs" / "2026-10-09_answer_quality" / "results.json"
LABELS = HERE / "eval_runs" / "causal_labels" / "labels.csv"
N, SEED = 15, 2026


def sample_ids():
    ids = sorted({r["report_id"] for r in csv.DictReader(LABELS.open())})
    return sorted(random.Random(SEED).sample(ids, N))


async def main(out):
    from backend.agents.enhanced_reporter import EnhancedReportGenerator
    from backend.agents.planner import resolve_llm_settings
    from backend.services.data_service import data_service

    await data_service.initialize()  # as the app's startup does
    gen = EnhancedReportGenerator()
    saved = {r["id"]: r for r in json.loads(RUN.read_text())["results"]}
    rows = []
    for rid in sample_ids():
        r = saved[rid]
        t0 = time.time()
        rep = await gen.generate_event_report(data=r["data"], prompt=r["plan"].get("report_prompt"),
                                              include_storyline=True, include_news=False, include_gkg=True)
        text = "\n".join([rep.summary, *rep.key_findings])
        checks = rep.checks or {}
        rows.append({"id": rid, "query": r["query"], "plan": r["plan"], "data": r["data"],
                     "status": "pass" if checks.get("passed") else "fail", "checks": checks,
                     "report": text, "model_input": gen.last_model_input, "elapsed_s": round(time.time() - t0, 1)})
        print(f"{rid}: checks {'passed' if checks.get('passed') else 'FAILED'} after {checks.get('attempts')} "
              f"attempt(s), {len(text)} chars, {rows[-1]['elapsed_s']} s", flush=True)
    summary = {"model": resolve_llm_settings()["model"], "reports": len(rows),
               "passed_first": sum(r["checks"].get("attempts") == 1 and r["checks"].get("passed") for r in rows),
               "rewritten": sum(r["checks"].get("attempts") == 2 for r in rows),
               "fell_back": sum(bool(r["checks"].get("fallback")) for r in rows),
               "mean_chars": round(sum(len(r["report"]) for r in rows) / max(len(rows), 1))}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"summary": summary, "results": rows}, indent=1))
    print(json.dumps(summary, indent=2))
    await data_service.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    asyncio.run(main(p.parse_args().out))
