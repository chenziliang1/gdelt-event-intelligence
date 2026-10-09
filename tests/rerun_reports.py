"""
Regenerate the reports of a saved answer-quality run with the current report prompt.

Each saved row keeps the plan and the exact data the report model was given, so only the prompt
changes: no planner or database calls, one report call per scored row (paid LLM requests).

    python tests/rerun_reports.py --run tests/eval_runs/2026-10-09_answer_quality/results.json \
        --out tests/eval_runs/2026-10-09_report_prompt/results.json

The output has the same shape as run_answer_quality_eval.py, so causal_judge.py --run can read it.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from answer_quality import check_report, report_text  # noqa: E402


async def rerun(run_path, out_path, concurrency):
    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env")
    from backend.agents.planner import ReportGenerator, resolve_llm_settings

    saved = json.loads(Path(run_path).read_text())
    rows = [r for r in saved["results"] if r["status"] in ("pass", "fail")]
    reporter = ReportGenerator()
    sem = asyncio.Semaphore(concurrency)

    async def one(r):
        async with sem:
            rep = await reporter.generate(r["data"], r["plan"].get("report_prompt"))
        text = report_text(rep.model_dump())
        checks = check_report(text, r["plan"], r["data"])
        print(f"[{'PASS' if checks['pass'] else 'FAIL'}] {r['id']}", flush=True)
        return {**r, "status": "pass" if checks["pass"] else "fail", "checks": checks, "report": text}

    results = await asyncio.gather(*(one(r) for r in rows))
    summary = {"rerun_of": str(run_path), "model": resolve_llm_settings()["model"], "report_calls": len(results),
               "scored": len(results), "passed": sum(r["status"] == "pass" for r in results),
               "causal_candidates": sum(len(r["checks"].get("causal_candidates", [])) for r in results)}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--concurrency", type=int, default=4)
    a = p.parse_args()
    asyncio.run(rerun(a.run, a.out, a.concurrency))
