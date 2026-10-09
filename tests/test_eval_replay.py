"""
The live agent evaluation, replayed in CI.

Every question in the eval sets goes through the real Planner, with the router replaced by its
recorded output: tests/fixtures/router_cassette.json (qwen2.5:3b, the fallback router) and, once
recorded, tests/fixtures/router_cassette_claude.json (the default Claude router). The plan is
scored with the same function as tests/run_agent_eval.py. No Ollama, database or LLM key needed.
What this does not cover: the executor and database (run_agent_eval.py does, live), and router
behaviour other than the recorded sample.
"""
import json
from pathlib import Path

import pytest

from backend.agents.planner import Planner, QueryContext
from run_agent_eval import score_item

HERE = Path(__file__).parent
OLD_SETS = ["agent_eval_set.json", "agent_eval_heldout.json", "agent_eval_heldout_v2.json"]
CASSETTES = {p.stem: json.loads(p.read_text()) for p in sorted((HERE / "fixtures").glob("router_cassette*.json"))}
CASES = [
    (cassette_name, set_name, item)
    for cassette_name, cassette in CASSETTES.items()
    for set_name in cassette.get("sets", OLD_SETS)
    for item in json.loads((HERE / set_name).read_text())["items"]
]


class CassetteRouter:
    def __init__(self, contexts):
        self.contexts = contexts

    async def extract_context(self, query):
        return QueryContext(**self.contexts[query])


@pytest.mark.parametrize("cassette_name,set_name,item", CASES,
                         ids=[f"{c}:{s.split('.')[0]}:{i['id']}" for c, s, i in CASES])
async def test_eval_item_passes_on_the_recorded_router_output(cassette_name, set_name, item, monkeypatch):
    planner = Planner()
    planner.router = CassetteRouter(CASSETTES[cassette_name]["contexts"])

    async def no_remote_llm(query, ctx):  # an item that needs the remote LLM fails here, visibly
        raise AssertionError("planner fell back to the remote LLM")

    monkeypatch.setattr(planner, "_llm_plan", no_remote_llm)
    plan, phases = await planner.plan(item["query"])
    body = {"ok": True, "phases": phases, "plan": {
        "intent": plan.intent, "notice": plan.notice, "routing_confidence": plan.routing_confidence,
        "steps": [{"type": s.type, "params": s.params} for s in plan.steps]}}
    verdict = score_item(item, {"http_status": 200, "body": body})
    assert verdict["pass"] is not False, verdict["actual"]
