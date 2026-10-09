"""
How the live safety net would have handled the saved reports, with real rewrite calls.

Every saved report of the given runs is re-checked with the current checks. A report that fails
is sent back once, exactly as ReportGenerator.generate does (current system prompt, the saved
report as the model's first answer, then the rewrite request), and the rewrite is checked again.
One LLM call per failing report; reports that pass cost nothing.

    python tests/eval_report_rewrite.py --runs 2026-10-08_answer_quality,2026-10-09_answer_quality \
        --out tests/eval_runs/2026-10-09_report_safety_net/results.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]


async def main(runs, out):
    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env")
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from backend.agents.planner import REPORT_SYSTEM_PROMPT, ReportGenerator, resolve_llm_settings
    from backend.services.report_checks import check_report, failed_checks, rewrite_request

    gen = ReportGenerator()
    rows, calls = [], 0
    for run in runs:
        for r in json.loads((HERE / "eval_runs" / run / "results.json").read_text())["results"]:
            if r.get("status") not in ("pass", "fail"):
                continue
            first = check_report(r["report"], r["plan"], r["data"])
            row = {"run": run, "id": r["id"], "first_passed": first["pass"], "failed_first": failed_checks(first)}
            if not first["pass"]:
                messages = [SystemMessage(content=REPORT_SYSTEM_PROMPT),
                            HumanMessage(content=f"{r['plan'].get('report_prompt') or 'Summarize these events.'}\n\n"
                                                 f"Event Data:\n{gen._format_data_for_report(r['data'])}\n\nWrite the summary:"),
                            AIMessage(content=r["report"]),
                            HumanMessage(content=rewrite_request(first, r["data"]))]
                rewrite = await gen._ask(messages)
                calls += 1
                second = check_report(rewrite, r["plan"], r["data"])
                row.update(rewrite=rewrite, rewrite_passed=second["pass"], failed_after=failed_checks(second))
                print(f"{run} {r['id']}: {sorted(row['failed_first'])} -> {'pass' if second['pass'] else sorted(row['failed_after'])}",
                      flush=True)
            rows.append(row)
    failing = [x for x in rows if not x["first_passed"]]
    summary = {"model": resolve_llm_settings()["model"], "runs": runs, "reports": len(rows),
               "passed_first": len(rows) - len(failing), "rewritten": len(failing),
               "rewrite_passed": sum(x["rewrite_passed"] for x in failing),
               "would_fall_back": sum(not x["rewrite_passed"] for x in failing), "llm_calls": calls}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"summary": summary, "results": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--runs", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    asyncio.run(main(a.runs.split(","), a.out))
