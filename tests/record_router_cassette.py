"""
Record what the real router (local Ollama qwen2.5:3b) returns for every eval question, so CI can
replay the planner on those exact router outputs without Ollama, MySQL or an LLM key.

    python tests/record_router_cassette.py          # needs Ollama running; writes tests/fixtures/router_cassette.json

Re-record after changing the router prompt or model. The 3B router is not fully deterministic
(the same sentence got different labels on reruns), so one recording is a sample of its behaviour,
not its only behaviour; tests/test_eval_replay.py states which recording it replays.
"""

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]

from backend.agents.planner import OllamaRouter  # noqa: E402

SETS = ("agent_eval_set.json", "agent_eval_heldout.json", "agent_eval_heldout_v2.json")


async def main() -> None:
    router = OllamaRouter()
    queries = []
    for name in SETS:
        for item in json.loads((HERE / name).read_text())["items"]:
            queries.append(item["query"])
    recorded = {}
    for q in dict.fromkeys(queries):
        ctx = await router.extract_context(q)
        recorded[q] = ctx.model_dump()
        print(f"{ctx.intent_category:9s} {q}")
    out = HERE / "fixtures" / "router_cassette.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"recorded_on": date.today().isoformat(), "router": "qwen2.5:3b via Ollama",
                               "contexts": recorded}, indent=1, ensure_ascii=False))
    print(f"{len(recorded)} router outputs written to {out}")


if __name__ == "__main__":
    asyncio.run(main())
