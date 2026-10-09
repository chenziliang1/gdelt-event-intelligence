"""
Evaluation harness for the Planner/Executor agent behind /api/v1/analyze.

Runs the hand-labeled question set in agent_eval_set.json against a live
backend and reports pass/fail per item plus a summary. This is a mini-POC
scoring script (POC-01), not a production eval platform: no historical
tracking, no CI wiring, just a repeatable way to tell whether a change to
the Planner made things better or worse.

Usage:
    python tests/run_agent_eval.py [--base-url http://localhost:8000]
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


def call_analyze(base_url: str, query: str, timeout: int = 30) -> dict:
    url = f"{base_url}/api/v1/analyze"
    payload = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"http_status": resp.status, "body": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body.decode("utf-8", errors="replace")}
        return {"http_status": e.code, "body": parsed}
    except Exception as e:
        return {"http_status": None, "body": {"error": str(e)}}


def extract_confidence(phases: list) -> str | None:
    for p in phases:
        if p.get("name") == "Context Extraction":
            detail = p.get("detail", "") or ""
            if "high confidence" in detail:
                return "high"
            if "low confidence" in detail or "low)" in detail:
                return "low"
            return "unknown"
    return None


def extract_step_types(plan: dict) -> list:
    return [s.get("type") for s in (plan or {}).get("steps", [])]


def extract_date_range(plan: dict) -> tuple | None:
    """(start, end) of the first step that carries a date range."""
    for s in (plan or {}).get("steps", []):
        params = s.get("params", {})
        if params.get("start_date") and params.get("end_date"):
            return params["start_date"], params["end_date"]
    return None


def extract_periods(plan: dict) -> list | None:
    """[(start, end), (start, end)] from a compare_periods step."""
    for s in (plan or {}).get("steps", []):
        periods = s.get("params", {}).get("periods")
        if periods:
            return [(p.get("start"), p.get("end")) for p in periods]
    return None


def extract_date_range_days(plan: dict) -> int | None:
    """Length of the range in days, INCLUSIVE of both ends (one day = 1, a Mon-Sun week = 7).

    The first version of this harness used end - start, which made a single day 0 and a
    week 6 while the eval set expected 1 for "yesterday": the expectations were
    inconsistent with each other. Inclusive counting is the one reading under which
    all of them make sense, and it is what a user means by "the past 3 days".
    """
    rng = extract_date_range(plan)
    if not rng:
        return None
    try:
        from datetime import date

        return (date.fromisoformat(rng[1]) - date.fromisoformat(rng[0])).days + 1
    except Exception:
        return None


def score_item(item: dict, result: dict) -> dict:
    body = result["body"]
    ok = body.get("ok", result["http_status"] == 200)
    plan = body.get("plan", {})
    phases = body.get("phases", [])
    expected = item["expected"]
    verdict = {"id": item["id"], "query": item["query"], "http_status": result["http_status"], "ok": ok}

    if not ok:
        verdict["pass"] = expected.get("graceful_not_found", False)
        verdict["actual"] = {"error": body.get("error") or body.get("detail")}
        return verdict

    actual_intent = plan.get("intent")
    actual_steps = extract_step_types(plan)
    confidence = extract_confidence(phases)
    date_range_days = extract_date_range_days(plan)
    date_range = extract_date_range(plan)
    periods = extract_periods(plan)
    notice = plan.get("notice")

    verdict["actual"] = {
        "intent": actual_intent,
        "step_types": actual_steps,
        "confidence": confidence,
        "date_range_days": date_range_days,
        "date_range": list(date_range) if date_range else None,
        "periods": [list(p) for p in periods] if periods else None,
        "notice": notice,
        "routing_confidence": plan.get("routing_confidence"),
    }

    checks = []

    if "intent" in expected:
        checks.append(actual_intent == expected["intent"])

    if "no_db_query" in expected:
        checks.append(len(actual_steps) == 0)

    if "step_types" in expected:
        checks.append(any(st in actual_steps for st in expected["step_types"]))

    if "assert_confidence_field" in expected:
        checks.append(confidence in ("high", "low"))

    if "assert_date_range_days" in expected and expected["assert_date_range_days"] is not None:
        checks.append(date_range_days == expected["assert_date_range_days"])

    if "assert_date_range" in expected:
        checks.append(list(date_range or []) == expected["assert_date_range"])

    if "assert_periods" in expected:
        checks.append([list(p) for p in (periods or [])] == expected["assert_periods"])

    if expected.get("assert_notice"):
        checks.append(bool(notice))

    if "assert_within_supported_range" in expected:
        lo, hi = expected["assert_within_supported_range"]
        for s in plan.get("steps", []):
            p = s.get("params", {})
            sd, ed = p.get("start_date"), p.get("end_date")
            if sd and ed:
                checks.append(lo <= sd <= hi and lo <= ed <= hi)

    verdict["pass"] = all(checks) if checks else None
    verdict["checks_run"] = len(checks)
    return verdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--eval-set",
        default=str(Path(__file__).parent / "agent_eval_set.json"),
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "eval_runs" / "latest.json"),
    )
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    items = eval_set["items"]

    results = []
    for item in items:
        t0 = time.time()
        result = call_analyze(args.base_url, item["query"])
        elapsed = time.time() - t0
        verdict = score_item(item, result)
        verdict["elapsed_s"] = round(elapsed, 2)
        results.append(verdict)
        status = "PASS" if verdict["pass"] else ("SKIP" if verdict["pass"] is None else "FAIL")
        print(f"[{status}] {item['id']:24s} {item['query'][:50]:50s} ({elapsed:.1f}s)")

    passed = sum(1 for r in results if r["pass"] is True)
    failed = sum(1 for r in results if r["pass"] is False)
    skipped = sum(1 for r in results if r["pass"] is None)

    summary = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped_no_assertion": skipped,
        "results": results,
    }

    # Calibration: is the evidence-based routing confidence related to being right?
    by_conf = {}
    for r in results:
        conf = (r.get("actual") or {}).get("routing_confidence")
        if conf and r["pass"] is not None:
            by_conf.setdefault(conf, []).append(bool(r["pass"]))
    summary["pass_rate_by_routing_confidence"] = {
        k: {"items": len(v), "passed": sum(v)} for k, v in sorted(by_conf.items())}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(summary, indent=2))
    print(f"\n{passed}/{len(results)} passed, {failed} failed, {skipped} had no strict assertion.")
    print("by routing confidence:", summary["pass_rate_by_routing_confidence"])
    print(f"Full results written to {args.out}")


if __name__ == "__main__":
    main()
