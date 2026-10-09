"""
Answer-quality evaluation: does the LLM-written report stay faithful to the data it was given?

For every question in the eval sets that returns data, call /api/v1/analyze, then
/api/v1/analyze/report exactly as the frontend does (data + plan.report_prompt), and run the
deterministic checks in tests/answer_quality.py. Every report call is a paid LLM request.

    python tests/run_answer_quality_eval.py [--base-url http://localhost:8000]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from answer_quality import check_report, report_text  # noqa: E402

HERE = Path(__file__).parent


def post(base_url: str, path: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        f"{base_url}{path}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": resp.status, "body": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": {"error": e.read().decode(errors="replace")[:500]}}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "body": {"error": str(e)}}


def has_rows(data: dict) -> bool:
    for v in (data or {}).values():
        d = v.get("data") if isinstance(v, dict) else v
        if d:
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--sets", default="agent_eval_set.json,agent_eval_heldout.json")
    p.add_argument("--out", default=str(HERE / "eval_runs" / "answer_quality_latest.json"))
    args = p.parse_args()

    items = []
    for name in args.sets.split(","):
        for item in json.loads((HERE / name).read_text())["items"]:
            if item.get("expected", {}).get("intent") == "off_topic":
                continue
            items.append((name, item))

    results, report_calls, planner_llm_calls = [], 0, 0
    for set_name, item in items:
        t0 = time.time()
        analyzed = post(args.base_url, "/api/v1/analyze", {"query": item["query"]})
        body = analyzed["body"]
        plan, data = body.get("plan") or {}, body.get("data") or {}
        planner_llm_calls += any(ph.get("name") == "AI Planning" for ph in body.get("phases", []))
        row = {"set": set_name, "id": item["id"], "query": item["query"],
               "step_types": [s.get("type") for s in plan.get("steps", [])]}
        if analyzed["status"] != 200 or not has_rows(data):
            row.update(status="skipped_no_data", analyze_status=analyzed["status"])
            results.append(row)
            print(f"[SKIP] {item['id']:22s} no data")
            continue
        report_calls += 1
        rep = post(args.base_url, "/api/v1/analyze/report", {"data": data, "prompt": plan.get("report_prompt")})
        text = report_text(rep["body"]) if rep["status"] == 200 else ""
        if rep["status"] != 200 or not text.strip() or text.startswith("No report content"):
            row.update(status="report_error", report_status=rep["status"], error=str(rep["body"])[:300])
            results.append(row)
            print(f"[ERR ] {item['id']:22s} report failed ({rep['status']})")
            continue
        checks = check_report(text, plan, data)
        # plan and data are kept so the checks can be re-run on these reports without new LLM calls.
        row.update(status="pass" if checks["pass"] else "fail", checks=checks, report=text,
                   plan=plan, data=data, elapsed_s=round(time.time() - t0, 1))
        results.append(row)
        flags = [k for k in ("ungrounded_numbers", "sample_as_total", "qualitative_trend", "dates_outside_window") if checks[k]]
        if checks["comparison_direction_ok"] is False:
            flags.append("comparison_direction")
        print(f"[{'PASS' if checks['pass'] else 'FAIL'}] {item['id']:22s} {', '.join(flags)}")

    scored = [r for r in results if r["status"] in ("pass", "fail")]
    summary = {
        "report_calls": report_calls,
        "planner_llm_calls": planner_llm_calls,
        "scored": len(scored),
        "passed": sum(r["status"] == "pass" for r in scored),
        "skipped_no_data": sum(r["status"] == "skipped_no_data" for r in results),
        "report_errors": sum(r["status"] == "report_error" for r in results),
        "failures_by_check": {
            "ungrounded_numbers": sum(bool(r["checks"]["ungrounded_numbers"]) for r in scored),
            "sample_as_total": sum(bool(r["checks"]["sample_as_total"]) for r in scored),
            "qualitative_trend": sum(bool(r["checks"]["qualitative_trend"]) for r in scored),
            "comparison_direction": sum(r["checks"]["comparison_direction_ok"] is False for r in scored),
            "dates_outside_window": sum(bool(r["checks"]["dates_outside_window"]) for r in scored),
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"Full results written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
