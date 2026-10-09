"""Planner and executor behaviour with no Ollama, no LLM and no database.

The router (local Qwen) is replaced by a stub that returns a *deliberately
wrong* context where that is the point of the test, because the bugs these
tests pin down were all "the router was confidently wrong and nothing checked".
"""
import pytest

from backend.agents.planner import (
    OllamaRouter,
    Planner,
    QueryContext,
    QueryPlan,
    QueryStep,
)
from backend.services.executor import Executor
from backend.queries.core_queries import _build_optimized_search_sql, query_period_counts


class StubRouter:
    """Stands in for OllamaRouter. ``ctx`` is what the 'LLM' returned."""

    def __init__(self, ctx=None, explode=False):
        self.ctx = ctx
        self.explode = explode
        self.calls = 0

    async def extract_context(self, query):
        self.calls += 1
        if self.explode:
            raise AssertionError("router must not be called on this path")
        return self.ctx.model_copy()


def planner_with(ctx=None, explode=False):
    p = Planner()
    p.router = StubRouter(ctx, explode)
    return p


# --- event IDs: used to be misrouted to a generic search ---------------------

@pytest.mark.parametrize(
    "query",
    [
        "tell me about event 1150442224",
        "what happened in event EVT-2024-01-09-1150442224",
        "tell me about event 9999999999",
    ],
)
async def test_event_id_in_a_sentence_goes_straight_to_event_detail(query):
    p = planner_with(explode=True)
    plan, phases = await p.plan(query)
    assert plan.steps[0].type == "event_detail"
    assert p.router.calls == 0
    assert phases[0]["name"] == "Event ID Match"


async def test_dates_are_not_mistaken_for_event_ids():
    ctx = QueryContext(intent_category="search", location="Texas", event_type="protest")
    plan, _ = await planner_with(ctx).plan("protests in Texas on 20240109")
    assert plan.steps[0].type == "events"


# --- greetings -------------------------------------------------------------

@pytest.mark.parametrize("query", ["hi", "thanks!", "???"])
async def test_greetings_never_reach_the_router(query):
    p = planner_with(explode=True)
    plan, _ = await p.plan(query)
    assert plan.intent == "off_topic" and plan.steps == []


# --- dates: the router was confidently wrong ---------------------------------

async def test_last_week_overrides_a_wrong_router_window():
    wrong = QueryContext(
        intent_category="search", location="Texas", event_type="protest",
        date_start="2024-01-01", date_end="2024-05-11",  # the 131-day window seen in the eval
    )
    plan, _ = await planner_with(wrong).plan("protests in Texas last week")
    params = plan.steps[0].params
    assert (params["start_date"], params["end_date"]) == ("2024-12-23", "2024-12-29")
    assert plan.notice and "anchored" in plan.notice


async def test_invalid_router_dates_are_dropped_and_the_user_is_told():
    bad = QueryContext(
        intent_category="search", location="Texas",
        date_start="2024-02-30", date_end="2024-03-01",
    )
    plan, phases = await planner_with(bad).plan("something happened in Texas")
    params = plan.steps[0].params
    assert (params["start_date"], params["end_date"]) == ("2024-01-01", "2024-12-31")
    assert plan.notice and "could not be interpreted" in plan.notice
    assert "invalid" in phases[0]["detail"]


@pytest.mark.parametrize("router_intent", ["search", "brief"])
async def test_impossible_date_in_the_question_is_not_silently_repaired(router_intent):
    # Live eval: the router turned 2024-02-30 into 2024-02-29 and nothing said so. A later
    # live run (router said brief) queried 2024-12-30 while the notice claimed the full year.
    repaired = QueryContext(intent_category=router_intent, location="Texas",
                            date_start="2024-02-29", date_end="2024-02-29")
    plan, phases = await planner_with(repaired).plan("what happened in Texas on 2024-02-30")
    params = plan.steps[0].params
    assert (params["start_date"], params["end_date"]) == ("2024-01-01", "2024-12-31")
    assert "2024-02-30" in plan.notice and "not a real calendar date" in plan.notice
    assert plan.notice.endswith("Showing 2024-01-01 to 2024-12-31 instead.")
    assert "invalid" in phases[0]["detail"]


async def test_notice_names_the_window_actually_queried():
    # A brief without a location can only show one day; the notice must say which.
    ctx = QueryContext(intent_category="brief")
    plan, _ = await planner_with(ctx).plan("daily brief for 2024-02-30")
    assert plan.notice.endswith(f"Showing {plan.steps[0].params['query_date']} instead.")


async def test_month_only_query_covers_the_whole_month():
    ctx = QueryContext(intent_category="search", location="Texas", event_type="protest",
                       date_start="2024-03-01", date_end="2024-03-28")  # the March 28 bug, from the router
    plan, _ = await planner_with(ctx).plan("protests in Texas in March 2024")
    params = plan.steps[0].params
    assert (params["start_date"], params["end_date"]) == ("2024-03-01", "2024-03-31")


async def test_protest_march_is_not_treated_as_a_month():
    ctx = QueryContext(intent_category="search", location="Texas", event_type="protest")
    plan, _ = await planner_with(ctx).plan("protest march in Texas")
    assert plan.steps[0].params["start_date"] == "2024-01-01"  # full period, not March


async def test_daily_brief_default_date_is_not_the_wall_clock():
    ctx = QueryContext(intent_category="brief")
    plan, _ = await planner_with(ctx).plan("give me the daily brief")
    assert plan.steps[0].params["query_date"] == "2024-12-30"


def test_fast_path_month_end_comes_from_the_calendar():
    import asyncio

    for text, end in [("protests in feb 2024", "2024-02-29"), ("events in april", "2024-04-30"), ("events in march", "2024-03-31")]:
        ctx = asyncio.run(OllamaRouter().extract_context_fast(text))
        assert ctx.date_end == end, text


# --- comparison --------------------------------------------------------------

async def test_did_protests_increase_plans_a_two_period_count_not_a_sample():
    # The router returns a plausible context for the sentence the interview used.
    ctx = QueryContext(intent_category="search", location="Canada", event_type="protest")
    plan, phases = await planner_with(ctx).plan("Did protest activity increase in Canada this month?")
    step = plan.steps[0]
    assert step.type == "compare_periods"
    assert [p["start"] for p in step.params["periods"]] == ["2024-11-01", "2024-12-01"]
    assert [p["end"] for p in step.params["periods"]] == ["2024-11-30", "2024-12-31"]
    assert step.params["location_hint"] == "Canada" and step.params["event_type"] == "protest"
    assert "anchored" in plan.notice
    assert phases[-1]["name"] == "Comparison Planning"


async def test_named_months_compare_in_chronological_order():
    ctx = QueryContext(intent_category="search", location="Canada", event_type="protest")
    plan, _ = await planner_with(ctx).plan("Did protests increase in Canada in March compared with February?")
    periods = plan.steps[0].params["periods"]
    assert (periods[0]["start"], periods[0]["end"]) == ("2024-02-01", "2024-02-29")
    assert (periods[1]["start"], periods[1]["end"]) == ("2024-03-01", "2024-03-31")


async def test_comparison_recovers_location_when_the_router_gave_none():
    ctx = QueryContext(intent_category="search")
    plan, _ = await planner_with(ctx).plan("Did protests increase in Canada this month?")
    assert plan.steps[0].params["location_hint"] == "Canada"
    assert plan.steps[0].params["event_type"] == "protest"


async def test_comparison_does_not_depend_on_the_router_label():
    # Live eval: the router labelled this question a daily brief.
    ctx = QueryContext(intent_category="brief", location="Canada", event_type="protest")
    plan, _ = await planner_with(ctx).plan("Did protests increase in Canada in March compared with February?")
    assert plan.steps[0].type == "compare_periods"


async def test_plain_search_is_not_turned_into_a_comparison():
    ctx = QueryContext(intent_category="search", location="Texas", event_type="protest")
    plan, _ = await planner_with(ctx).plan("show me protests in Texas last week")
    assert plan.steps[0].type == "events"


# --- "how has <region> been": a regional overview ---------------------------------

@pytest.mark.parametrize("router_intent", ["brief", "search"])
async def test_how_has_a_region_been_is_an_overview(router_intent):
    # Live eval: the router said brief for California and search for Texas.
    ctx = QueryContext(intent_category=router_intent, location="California")
    plan, _ = await planner_with(ctx).plan("how has California been this month")
    step = plan.steps[0]
    assert step.type == "regional_overview"
    assert (step.params["start_date"], step.params["end_date"]) == ("2024-12-01", "2024-12-31")


async def test_how_has_recovers_a_location_the_router_dropped():
    # Live eval: for California the router returned location=None, query_text='California this month'.
    ctx = QueryContext(intent_category="brief", query_text="California this month")
    plan, _ = await planner_with(ctx).plan("how has California been this month")
    assert plan.steps[0].type == "regional_overview"
    assert plan.steps[0].params["region"] == "California"


async def test_how_has_without_a_location_is_left_alone(monkeypatch):
    # No place, so not a regional overview. No briefing word either, so (since the label check)
    # not a brief: it is an underspecified search and goes to the remote fallback, stubbed here.
    ctx = QueryContext(intent_category="brief")
    p = planner_with(ctx)

    async def fake_llm_plan(query, c):
        return QueryPlan(intent="llm", steps=[QueryStep(type="events", params={})], visualizations=[])

    monkeypatch.setattr(p, "_llm_plan", fake_llm_plan)
    plan, _ = await p.plan("how has it been going")
    assert plan.steps[0].type != "regional_overview"


# --- regex fallback ------------------------------------------------------------

@pytest.mark.parametrize(
    "query,intent",
    [
        ("give me today's daily brief", "brief"),
        ("give me a regional overview of Texas this year", "overview"),
        ("what's hot right now", "hot"),
        ("protests in Texas", "search"),
    ],
)
def test_regex_fallback_detects_intent(query, intent):
    assert OllamaRouter()._fallback_extract_context(query).intent_category == intent


def test_fallback_month_year_uses_the_real_last_day():
    ctx = OllamaRouter()._fallback_extract_context("protests in Texas feb 2024")
    assert (ctx.date_start, ctx.date_end) == ("2024-02-01", "2024-02-29")


# --- the remote LLM fallback must be reachable ---------------------------------

def test_underspecified_search_is_handed_to_the_fallback():
    ctx = QueryContext(intent_category="search", confidence="low", query_text="stuff")
    assert Planner()._rule_based_plan(ctx) is None


def test_free_text_keyword_search_is_not_ambiguous():
    ctx = QueryContext(intent_category="search", confidence="low",
                       query_text="find news articles about the Uvalde school shooting response")
    assert Planner()._rule_based_plan(ctx) is not None


def test_specified_search_stays_on_the_rule_path():
    ctx = QueryContext(intent_category="search", location="Texas", confidence="low", query_text="x")
    assert Planner()._rule_based_plan(ctx) is not None


async def test_remote_fallback_is_actually_called_for_an_ambiguous_question(monkeypatch):
    ctx = QueryContext(intent_category="search", confidence="low", query_text="tell me something")
    p = planner_with(ctx)
    called = {}

    async def fake_llm_plan(query, c):
        called["yes"] = True
        return QueryPlan(intent="llm", steps=[QueryStep(type="events", params={})], visualizations=[])

    monkeypatch.setattr(p, "_llm_plan", fake_llm_plan)
    plan, phases = await p.plan("tell me something")
    assert called and plan.intent == "llm"
    assert phases[-1]["name"] == "AI Planning"


# --- the router bug: NameError swallowed by `except` ----------------------------

async def test_router_does_not_swallow_a_name_error_when_query_text_is_missing(monkeypatch):
    """`user_input` was an undefined name, so any model reply without query_text raised
    NameError, was caught, and the router fell back to regex. Now it returns the model's context."""
    router = OllamaRouter()

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"response": '{"location": "Texas", "event_type": "protest", "intent_category": "search", "query_text": null}'}

    async def fake_post(url, json=None):
        return Resp()

    monkeypatch.setattr(router, "_client", type("C", (), {"post": staticmethod(fake_post)})())
    ctx = await router.extract_context("protests in Texas")
    assert ctx.confidence == "high" and ctx.location == "Texas"


# --- executor ------------------------------------------------------------------

class FakeDS:
    def __init__(self):
        self.calls = []

    async def compare_periods(self, **kw):
        self.calls.append(("compare_periods", kw))
        return {"verdict": "ok"}

    async def search_news_context(self, query, n_results=5):
        self.calls.append(("news", query, n_results))
        return {"articles": []}


async def test_executor_handles_compare_periods_and_news_context():
    ds = FakeDS()
    out = await Executor(ds).execute(QueryPlan(
        intent="x", visualizations=[],
        steps=[
            QueryStep(type="compare_periods", params={"periods": [{"a": 1}, {"b": 2}], "location_hint": "Canada", "event_type": "protest"}),
            QueryStep(type="news_context", params={"query": "wildfire", "n_results": 3}),
        ],
    ))
    assert out["compare_periods_0"]["data"] == {"verdict": "ok"}
    assert out["news_context_1"]["data"] == {"articles": []}
    assert ds.calls[0][1]["location_hint"] == "Canada"
    assert ds.calls[1] == ("news", "wildfire", 3)


# --- SQL: one definition of "protest" --------------------------------------------

def test_protest_is_defined_once_and_used_by_search_and_comparison():
    sql, _ = _build_optimized_search_sql("2024-01-01", "2024-01-31", "Texas", None, "protest", None, None, "rally", 20)
    assert "EventRootCode = '14'" in sql
    assert "e.EventRootCode = '14'" in sql  # keyword branch is alias-qualified


async def test_period_counts_use_the_same_filters_and_bind_every_value():
    captured = []

    class Pool:
        async def fetchone(self, sql, params):
            captured.append((sql, params))
            return {"event_count": 7, "article_count": 21}

    rows = await query_period_counts(
        Pool(),
        [{"label": "Nov", "start": "2024-11-01", "end": "2024-11-30"},
         {"label": "Dec", "start": "2024-12-01", "end": "2024-12-31"}],
        location_hint="Canada", event_type="protest",
    )
    assert [r["event_count"] for r in rows] == [7, 7]
    sql, params = captured[0]
    assert "EventRootCode = '14'" in sql and "COUNT(*)" in sql and "LIMIT" not in sql
    assert params[:2] == ("2024-11-01", "2024-11-30")
    assert sql.count("%s") == len(params)  # nothing interpolated into the SQL string


async def test_default_windows_in_sql_are_anchored_to_the_dataset_not_the_wall_clock():
    # Regional overview with no dates used datetime.now(): a 2026 window over 2024 data.
    from backend.queries.core_queries import query_regional_overview

    seen = []

    class Pool:
        async def fetchall(self, sql, params):
            return []

        async def fetchone(self, sql, params):
            seen.append(params)
            return {}

    out = await query_regional_overview(Pool(), "Texas", time_range="week")
    assert (out["start"], out["end"]) == ("2024-12-24", "2024-12-31")
    assert seen[0][2:] == ("2024-12-24", "2024-12-31")  # precomputed lookup: (code, name, start, end)
    assert out["source"] == "realtime"


async def test_precomputed_overview_covers_the_whole_range_in_the_report_shape():
    # It used to return the last 7 daily rows under "rows", which the report never reads.
    from backend.queries.core_queries import query_regional_overview

    calls = []

    class Pool:
        async def fetchone(self, sql, params):
            calls.append(sql)
            return {"region_type": "state", "region_code": "TEXAS", "region_name": "Texas", "total": 512345,
                    "avg_goldstein": 0.4, "avg_tone": -2.1, "conflicts": 9000, "cooperation": 30000, "days_with_data": 366}

        async def fetchall(self, sql, params):
            calls.append(sql)
            if "top_event_ids" in sql:  # each day's stored top 5
                return [{"top_event_ids": "[11, 12]"}, {"top_event_ids": [13]}]
            assert params == (11, 12, 13)  # hot events come from the candidates, no range scan
            return [{"SQLDATE": "2024-01-09"}]

    out = await query_regional_overview(Pool(), "Texas", start_date="2024-01-01", end_date="2024-12-31")
    assert out["source"] == "precomputed"
    assert out["summary"]["total"] == 512345 and set(out["summary"]) == {"total", "avg_goldstein", "avg_tone", "conflicts", "cooperation"}
    assert out["hot_events"] == [{"SQLDATE": "2024-01-09"}]
    assert "LIMIT 7" not in calls[0] and "SUM(event_count)" in calls[0]


async def test_hot_events_fall_back_when_the_stored_list_is_empty():
    # Live: the restored daily_summary held the string "[]" for all 366 days -> [] every day.
    from backend.queries.core_queries import query_hot_events

    class Pool:
        async def fetchone(self, sql, params):
            return {"hot_event_fingerprints": "[]", "top_actors": None, "top_locations": None}

        async def fetchall(self, sql, params):
            return [{"GlobalEventID": 1150442224}]

    assert await query_hot_events(Pool(), query_date="2024-01-09") == [{"GlobalEventID": 1150442224}]


async def test_hot_events_fall_back_when_fingerprints_cannot_be_resolved():
    # Fingerprints that event_fingerprints cannot resolve -> [] instead of the live query.
    from backend.queries.core_queries import query_hot_events

    class Pool:
        async def fetchone(self, sql, params):
            if "FROM daily_summary" in sql:
                return {"hot_event_fingerprints": '["US-20240109-TX-PROT-1"]', "top_actors": None, "top_locations": None}
            return None  # the fingerprint lookup finds nothing

        async def fetchall(self, sql, params):
            assert "LEFT JOIN event_fingerprints" in sql and params[0] == "2024-01-09"
            return [{"GlobalEventID": 1150442224}]

    assert await query_hot_events(Pool(), query_date="2024-01-09") == [{"GlobalEventID": 1150442224}]


def test_keyword_search_matches_topic_words_not_the_whole_sentence():
    from backend.queries.query_utils import keyword_terms

    assert keyword_terms("find news articles about the Uvalde school shooting response") == [
        "uvalde", "school", "shooting", "response"]
    assert keyword_terms("search for headline mentioning wildfire") == ["wildfire"]
    assert keyword_terms("find news") == []

    sql, params = _build_optimized_search_sql(
        "2024-01-01", "2024-12-31", None, None, None, None, None, "search for headline mentioning wildfire", 20)
    assert "MATCH(f.headline, f.source_text) AGAINST (%s IN BOOLEAN MODE)" in sql
    assert "+wildfire" in params and not any("%search for headline" in str(p) for p in params)
    assert sql.count("%s") == len(params)


# --- LLM provider settings -------------------------------------------------------

def test_claude_is_the_default_provider(monkeypatch):
    from backend.agents.planner import resolve_llm_settings

    for var in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    s = resolve_llm_settings()
    assert (s["provider"], s["base_url"], s["model"]) == ("claude", "https://api.anthropic.com/v1/", "claude-sonnet-5-5")


def test_generic_base_url_does_not_leak_into_another_provider(monkeypatch):
    # LLM_BASE_URL / LLM_MODEL used to apply to every provider, so a runtime switch sent the
    # new provider's key to the old provider's URL.
    from backend.agents.planner import resolve_llm_settings

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://example-openai-proxy.invalid/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    s = resolve_llm_settings({"provider": "claude", "api_key": "k"})
    assert s["base_url"] == "https://api.anthropic.com/v1/" and s["model"] == "claude-sonnet-5-5"
    assert resolve_llm_settings({"provider": "anthropic", "api_key": "k"})["provider"] == "claude"


def test_no_client_impersonation_headers(monkeypatch):
    # The removed Kimi path rewrote User-Agent to "claude-code/1.0". No provider gets that now.
    from backend.agents.planner import _LLM_PROVIDERS, build_llm

    assert set(_LLM_PROVIDERS) == {"claude", "openai"}

    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert build_llm().http_async_client is None


# --- held-out failures (tests/eval_runs/2026-10-08_heldout): router labels vs the user's words ---

async def test_router_detail_label_without_an_event_id_does_not_block_a_comparison():
    ctx = QueryContext(intent_category="detail", location="Texas", event_type="protest")
    plan, _ = await planner_with(ctx).plan("Were there more protests in Texas in October than in September?")
    periods = plan.steps[0].params["periods"]
    assert plan.steps[0].type == "compare_periods"
    assert (periods[0]["start"], periods[1]["end"]) == ("2024-09-01", "2024-10-31")


async def test_router_brief_label_needs_a_briefing_word():
    ctx = QueryContext(intent_category="brief", location="Washington DC")
    plan, _ = await planner_with(ctx).plan("news about the march on Washington DC")
    params = plan.steps[0].params
    assert plan.steps[0].type == "events"
    assert (params["start_date"], params["end_date"]) == ("2024-01-01", "2024-12-31")  # not yesterday


@pytest.mark.parametrize("router_intent", ["search", "overview"])  # the router gave both on reruns
async def test_recap_of_one_day_is_a_daily_brief(router_intent):
    ctx = QueryContext(intent_category=router_intent)
    plan, _ = await planner_with(ctx).plan("give me a recap of 2024-11-05")
    assert plan.steps[0].type == "daily_brief" and plan.steps[0].params["query_date"] == "2024-11-05"


async def test_this_week_is_cut_at_the_end_of_the_data_and_the_user_is_told():
    ctx = QueryContext(intent_category="search", location="Mexico", event_type="conflict")
    plan, _ = await planner_with(ctx).plan("conflict events in Mexico this week")
    params = plan.steps[0].params
    assert (params["start_date"], params["end_date"]) == ("2024-12-30", "2024-12-31")  # was ... to 2025-01-05
    assert "limited to the data available" in plan.notice


async def test_go_down_is_a_comparison():
    ctx = QueryContext(intent_category="search", location="Mexico", event_type="conflict")
    plan, _ = await planner_with(ctx).plan("Did conflict events in Mexico go down this month?")
    assert plan.steps[0].type == "compare_periods"


def test_claude_requests_do_not_send_temperature(monkeypatch):
    # claude-sonnet-5-5 answers 400 "`temperature` is deprecated for this model".
    from backend.agents.planner import build_llm

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert build_llm().temperature is None
    assert build_llm({"provider": "openai", "api_key": "k"}).temperature == 0.1


def test_report_request_has_the_field_the_route_reads():
    # Every /analyze/report call returned 500: the route reads request.llm_config and
    # ReportRequest had no such field (AttributeError before the LLM was called).
    from backend.schemas.responses import ReportRequest

    assert ReportRequest(data={}, prompt="p").llm_config is None
    cfg = ReportRequest(data={}, llm_config={"provider": "claude", "api_key": "k"}).llm_config
    assert cfg.provider == "claude"


async def test_report_endpoint_accepts_the_frontend_request(monkeypatch):
    pytest.importorskip("fastapi")
    import backend.routers.analyze as analyze_router
    from backend.schemas.responses import ReportRequest

    class FakeReporter:
        def __init__(self, config=None):
            self.config = config

        async def generate(self, data, prompt):
            from backend.agents.planner import ReportResult
            return ReportResult(summary=f"ok: {prompt}", key_findings=["a"])

    monkeypatch.setattr(analyze_router, "ReportGenerator", FakeReporter)
    out = await analyze_router.generate_report(ReportRequest(data={"events_0": {"data": []}}, prompt="p"))
    assert out.summary == "ok: p" and out.key_findings == ["a"]


async def test_forecast_inputs_use_the_definitions_the_model_was_trained_on():
    # Serving counted protest as roots 14-16 while the thp_* training tables count root 14
    # (US, 2024-03-05: 895 vs 470), so the protest forecast was fed about twice its training input.
    from backend.queries.core_queries import query_event_sequence
    from backend.queries.query_utils import FORECAST_EVENT_TYPE_CONDITIONS

    seen = []

    class Pool:
        async def fetchall(self, sql, params):
            seen.append(sql)
            return []

    await query_event_sequence(Pool(), "2024-03-01", "2024-03-31", event_type="protest")
    assert "EventRootCode = '14'" in seen[0] and "'15'" not in seen[0]
    train = pytest.importorskip("train_thp_model")  # needs torch + mysql.connector
    for name in ("conflict", "cooperation", "protest"):
        assert FORECAST_EVENT_TYPE_CONDITIONS[name] in train.metrics_sql()


async def test_no_comparison_with_a_period_outside_the_dataset():
    # Live: "December 2023 had none" for January; the data simply has no December 2023.
    ctx = QueryContext(intent_category="search", location="Canada", event_type="protest")
    plan, _ = await planner_with(ctx).plan("Did protests increase in Canada in January?")
    step = plan.steps[0]
    assert step.type == "events"
    assert (step.params["start_date"], step.params["end_date"]) == ("2024-01-01", "2024-01-31")
    assert "no data for December 2023" in plan.notice and "Showing January 2024 only" in plan.notice


# --- routing confidence from evidence ------------------------------------------------

async def test_routing_confidence_is_low_when_the_router_label_is_overridden():
    ctx = QueryContext(intent_category="detail", location="Texas", confidence="high")  # router "high"
    plan, phases = await planner_with(ctx).plan("Were there more protests in Texas in October than in September?")
    assert plan.routing_confidence == "low"
    assert any(p["name"] == "Routing Check" and "overridden" in p["detail"] for p in phases)


async def test_routing_confidence_is_medium_when_only_the_dates_were_corrected():
    wrong = QueryContext(intent_category="search", location="Texas", event_type="protest", confidence="high",
                         date_start="2024-01-01", date_end="2024-05-11")
    plan, _ = await planner_with(wrong).plan("protests in Texas last week")
    assert plan.routing_confidence == "medium"


async def test_routing_confidence_is_high_when_router_and_rules_agree():
    ctx = QueryContext(intent_category="search", location="Texas", event_type="protest", confidence="high",
                       date_start="2024-03-01", date_end="2024-03-31")
    plan, _ = await planner_with(ctx).plan("protests in Texas in March 2024")
    assert plan.routing_confidence == "high"


# --- held-out v2 failures (tests/eval_runs/2026-10-09_heldout_v2) ------------------------

async def test_a_canadian_fingerprint_is_an_event_lookup():
    p = planner_with(explode=True)
    plan, _ = await p.plan("explain CA-20240603-CRO-APPEAL-1179504660")
    assert [s.type for s in plan.steps] == ["event_detail", "similar_events"]
    assert plan.steps[1].params["seed_event_id"] == 1179504660


@pytest.mark.parametrize("router_intent", ["search", "brief"])  # live router said brief
async def test_biggest_news_on_a_day_is_hot_events(router_intent):
    ctx = QueryContext(intent_category=router_intent)
    plan, _ = await planner_with(ctx).plan("biggest news on 2024-07-04")
    assert plan.steps[0].type == "hot_events"


@pytest.mark.parametrize("query,place", [("overview of British Columbia this year", "British Columbia"),
                                         ("how is Jalisco doing in Q3", "Jalisco")])
async def test_overview_label_with_a_dropped_place_recovers_it(query, place):
    ctx = QueryContext(intent_category="overview")  # the router's label, without the place
    plan, _ = await planner_with(ctx).plan(query)
    assert plan.steps[0].type == "regional_overview" and plan.steps[0].params["region"] == place


# --- Claude router -----------------------------------------------------------------

async def test_claude_router_falls_back_to_the_local_router_without_a_key():
    """No ANTHROPIC_API_KEY (conftest empties it): no request is made, the local router answers,
    and the planner marks the routing as at most medium."""
    from backend.agents.planner import ClaudeRouter

    local = QueryContext(location="Texas", event_type="protest", intent_category="search", router="qwen2.5:3b")
    router = ClaudeRouter(fallback=StubRouter(local))
    ctx = await router.extract_context("protests in Texas in March")
    assert ctx.router == "qwen2.5:3b" and router.fallback.calls == 1

    p = Planner()
    p.router = router
    plan, phases = await p.plan("protests in Texas in March 2024")
    assert plan.routing_confidence == "medium"
    assert "Claude router unavailable" in phases[1]["detail"]


async def test_claude_router_reply_is_parsed_like_the_local_one(monkeypatch):
    from backend.agents.planner import ClaudeRouter

    class Reply:
        content = '```json\n{"location": "Québec", "date_start": "2024-04-01", "date_end": "2024-06-30", ' \
                  '"event_type": null, "query_text": null, "intent_category": "overview"}\n```'

    class FakeLLM:
        async def ainvoke(self, messages):
            return Reply()

    router = ClaudeRouter(fallback=StubRouter(explode=True))
    monkeypatch.setattr(router, "_client", lambda: FakeLLM())
    ctx = await router.extract_context("Give me an overview of Québec between April and June")
    assert (ctx.location, ctx.intent_category, ctx.router) == ("Québec", "overview", router.model)
    assert ctx.event_type is None
