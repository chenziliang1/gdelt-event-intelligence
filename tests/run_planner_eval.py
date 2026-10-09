"""
Score the eval sets against the real Planner with a live router, without the backend or database.

All expectations in the eval sets are about the plan (tools, dates, periods, notices), so the
planner alone decides them; tests/run_agent_eval.py runs the same items through the full API.

    python tests/run_planner_eval.py --router ollama --sets agent_eval_heldout_v3.json --out run.json
    python tests/run_planner_eval.py --router claude --out run.json   # one Claude call per question
    python tests/run_planner_eval.py --router cassette --cassette tests/fixtures/router_cassette_claude.json --out run.json

The remote planner fallback (_llm_plan, another paid call) is disabled: an item that would need it
is scored as failed, as in the CI replay. Each result records the routing confidence, so the
confidence levels can be checked against pass/fail.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

SETS = ("agent_eval_set.json", "agent_eval_heldout.json", "agent_eval_heldout_v2.json", "agent_eval_heldout_v3.json")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--router", choices=("ollama", "claude", "cassette"), required=True)
    p.add_argument("--cassette", help="Recorded router outputs (tests/record_router_cassette.py); no router calls.")
    p.add_argument("--sets", nargs="+", default=list(SETS))
    p.add_argument("--out", required=True)
    args = p.parse_args()
    os.environ["ROUTER_PROVIDER"] = "ollama" if args.router == "cassette" else args.router
    if args.router == "claude":
        from dotenv import load_dotenv
        load_dotenv(HERE.parent / ".env")

    from backend.agents.planner import Planner
    from run_agent_eval import score_item

    planner = Planner()
    if args.router == "cassette":
        from backend.agents.planner import ClaudeRouter, QueryContext

        contexts = json.loads(Path(args.cassette).read_text())["contexts"]

        class Replay(ClaudeRouter):  # a ClaudeRouter, so a recorded fallback is treated as one
            async def extract_context(self, query):
                return QueryContext(**contexts[query])

        planner.router = Replay()

    async def no_remote_planner(query, ctx):
        raise RuntimeError("needs the remote planner (disabled in this eval)")

    planner._llm_plan = no_remote_planner
    results, routers = [], Counter()
    for set_name in args.sets:
        for item in json.loads((HERE / set_name).read_text())["items"]:
            t0 = time.time()
            try:
                plan, phases = await planner.plan(item["query"])
                body = {"ok": True, "phases": phases, "plan": {
                    "intent": plan.intent, "notice": plan.notice, "routing_confidence": plan.routing_confidence,
                    "steps": [{"type": s.type, "params": s.params} for s in plan.steps]}}
                verdict = score_item(item, {"http_status": 200, "body": body})
                extraction = next((ph["detail"] for ph in phases if ph["name"] == "Context Extraction"), "")
                routers[extraction.split(" extracted")[0] if extraction else "fast path"] += 1
            except RuntimeError as e:
                verdict = {"id": item["id"], "query": item["query"], "pass": False, "actual": {"error": str(e)}}
            verdict.update(set=set_name, elapsed_s=round(time.time() - t0, 2))
            results.append(verdict)
            mark = {True: "PASS", False: "FAIL", None: "----"}[verdict["pass"]]
            conf = (verdict.get("actual") or {}).get("routing_confidence")
            print(f"[{mark}] {set_name.split('.')[0]:24s} {item['id']:20s} conf={conf} {json.dumps(verdict.get('actual'))[:200]}", flush=True)
            if verdict["pass"] is False:
                print(f"        expected {json.dumps(item['expected'])}", flush=True)
    await planner.router.close()

    summary = {}
    for set_name in args.sets:
        rs = [r for r in results if r["set"] == set_name]
        summary[set_name] = {"passed": sum(r["pass"] is True for r in rs), "failed": sum(r["pass"] is False for r in rs),
                             "no_assertion": sum(r["pass"] is None for r in rs)}
    by_conf = {}
    for r in results:
        c = (r.get("actual") or {}).get("routing_confidence")
        by_conf.setdefault(str(c), Counter())["pass" if r["pass"] else "fail" if r["pass"] is False else "none"] += 1
    out = {"router": args.router, "sets": args.sets, "summary": summary, "routers_used": dict(routers),
           "pass_by_routing_confidence": {k: dict(v) for k, v in by_conf.items()}, "results": results}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("summary", "routers_used", "pass_by_routing_confidence")}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
