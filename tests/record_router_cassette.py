"""
Record what a real router returns for every eval question, so CI can replay the planner on those
exact router outputs without Ollama, MySQL or an LLM key.

    python tests/record_router_cassette.py --router ollama   # needs Ollama; tests/fixtures/router_cassette.json
    python tests/record_router_cassette.py --router claude   # needs ANTHROPIC_API_KEY (one call per question);
                                                             # tests/fixtures/router_cassette_claude.json

Re-record after changing the router prompt or model. Neither router is fully deterministic (the
3B router gave the same sentence different labels on reruns), so one recording is a sample of its
behaviour, not its only behaviour; tests/test_eval_replay.py states which recordings it replays.
A Claude call that fails falls back to the local router; such entries are counted and must be 0.
"""

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from backend.agents.planner import ClaudeRouter, OllamaRouter  # noqa: E402

SETS = ("agent_eval_set.json", "agent_eval_heldout.json", "agent_eval_heldout_v2.json", "agent_eval_heldout_v3.json")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--router", choices=("ollama", "claude"), default="ollama")
    p.add_argument("--redo-fallbacks", action="store_true",
                   help="Claude only: re-record just the entries that fell back to another router.")
    args = p.parse_args()
    if args.router == "claude":
        from dotenv import load_dotenv
        load_dotenv(HERE.parent / ".env")
    router = ClaudeRouter() if args.router == "claude" else OllamaRouter()
    name = router.model if args.router == "claude" else f"{OllamaRouter.MODEL} via Ollama"
    sets = [s for s in SETS if (HERE / s).exists()]
    queries = [item["query"] for s in sets for item in json.loads((HERE / s).read_text())["items"]]
    out = HERE / "fixtures" / ("router_cassette_claude.json" if args.router == "claude" else "router_cassette.json")
    recorded, fell_back = {}, 0
    if args.redo_fallbacks:
        recorded = json.loads(out.read_text())["contexts"]
        queries = [q for q in queries if q not in recorded or recorded[q].get("router") != router.model]
    for q in dict.fromkeys(queries):
        ctx = await router.extract_context(q)
        recorded[q] = ctx.model_dump()
        fell_back += args.router == "claude" and ctx.router != router.model
        print(f"{ctx.intent_category:9s} {ctx.router:18s} {q}")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"recorded_on": date.today().isoformat(), "router": name, "sets": sets,
                               "contexts": recorded}, indent=1, ensure_ascii=False))
    print(f"{len(recorded)} router outputs written to {out}; fell back to another router: {fell_back}")
    await router.close()


if __name__ == "__main__":
    asyncio.run(main())
