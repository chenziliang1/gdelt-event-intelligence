"""The live report path: checks on every report, one rewrite, then a deterministic fallback.

A fake LLM returns scripted replies, so these tests make no API call.
"""
import pytest

from backend.agents.definitions import definitions_line
from backend.agents.planner import ReportGenerator, deterministic_summary
from backend.services.report_checks import ALL_CHECKS
from backend.schemas.responses import QueryPlanOutput, ReportOutput, ReportRequest

DATA = {"events_0": {"type": "events", "data": [
    {"SQLDATE": "2024-01-05", "NumArticles": 70, "Actor1Name": "POLICE",
     "ActionGeo_FullName": "Perry High School, Iowa, United States", "event_type_label": "Protest"},
    {"SQLDATE": "2024-01-05", "NumArticles": 70, "Actor1Name": "AUTHORITIES",
     "ActionGeo_FullName": "Perry High School, Iowa, United States", "event_type_label": "Protest"},
    {"SQLDATE": "2024-01-06", "NumArticles": 100, "Actor1Name": "IOWA",
     "ActionGeo_FullName": "Iowa, United States", "event_type_label": "Protest"},
]}}
PLAN = {"steps": [{"type": "events", "params": {"start_date": "2024-01-01", "end_date": "2024-01-31"}}]}

GOOD = "On 2024-01-06, an IOWA record drew 100 articles. Two records at Perry High School on 2024-01-05 drew 70 articles each."
BAD_NUMBER = "On 2024-01-06, an IOWA record drew 140 articles."
BAD_COUNT = "Four records at Perry High School on 2024-01-05 drew 70 articles each."


class ScriptedLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply

        class Response:
            content = reply
        return Response()


def reporter(replies):
    r = ReportGenerator.__new__(ReportGenerator)  # no real LLM client
    r.llm = ScriptedLLM(replies)
    return r


async def test_a_report_that_passes_is_returned_after_one_call():
    r = reporter([GOOD])
    out = await r.generate(DATA, "Summarize.", PLAN)
    assert out.summary == GOOD and len(r.llm.calls) == 1
    assert out.checks == {"checked": list(ALL_CHECKS), "passed": True, "attempts": 1, "fallback": False,
                          "failed_first": {}, "failed": {}}


async def test_a_failing_report_is_rewritten_once_with_what_failed():
    r = reporter([BAD_NUMBER, GOOD])
    out = await r.generate(DATA, "Summarize.", PLAN)
    assert out.summary == GOOD and len(r.llm.calls) == 2
    assert out.checks["passed"] and out.checks["attempts"] == 2
    assert out.checks["failed_first"] == {"ungrounded_numbers": ["140"]}
    rewrite = r.llm.calls[1][-1].content  # the feedback names the number and the rule
    assert '"140"' in rewrite and "not in the data" in rewrite


async def test_failing_twice_falls_back_to_the_records():
    r = reporter([BAD_COUNT, BAD_NUMBER])
    out = await r.generate(DATA, "Summarize.", PLAN)
    assert out.checks["fallback"] and not out.checks["passed"] and out.checks["attempts"] == 2
    assert out.checks["failed_first"] == {"count_overclaims": [BAD_COUNT]}
    assert "did not pass the automatic checks (numbers not in the data)" in out.summary
    assert "2024-01-06 · Iowa, United States · IOWA · Protest · 100 articles" in out.summary
    assert BAD_NUMBER not in out.summary and out.key_findings == []


async def test_a_rewrite_that_errors_falls_back_instead_of_showing_the_failing_report():
    r = reporter([BAD_NUMBER, TimeoutError()])
    out = await r.generate(DATA, "Summarize.", PLAN)
    assert out.checks["fallback"] and BAD_NUMBER not in out.summary


async def test_without_a_plan_the_checks_still_run_on_the_data():
    out = await reporter([BAD_NUMBER, GOOD]).generate(DATA, "Summarize.")
    assert out.checks["attempts"] == 2 and out.checks["passed"]


def test_fallback_states_a_comparison_from_the_computed_counts():
    data = {"compare_periods_0": {"type": "compare_periods", "data": {
        "verdict": "increase", "percent_change_per_day": 30.1,
        "earlier": {"label": "February 2024", "event_count": 858, "events_per_day": 29.59},
        "later": {"label": "March 2024", "event_count": 1193, "events_per_day": 38.48}}}}
    text = deterministic_summary(data, ["comparison_direction"])
    assert "Period comparison, increase: February 2024: 858 events (29.59 per day); March 2024: 1,193 events" in text
    assert "+30.1%" in text


def test_definitions_line_names_the_unit_the_place_and_the_anchor():
    line = definitions_line([{"type": "events", "params": {"event_type": "protest"}}])
    assert line.startswith("Definitions: Counts are GDELT event records, not articles")
    assert "protest = CAMEO root code 14" in line and "a sample, not totals" in line
    assert "relative dates are resolved as of 2024-12-31" in line
    # A comparison has no sample.
    assert "sample" not in definitions_line([{"type": "compare_periods", "params": {}}])


def test_the_api_schemas_carry_the_plan_the_checks_and_the_definitions():
    assert ReportRequest(data={}, plan=PLAN).plan == PLAN
    assert ReportOutput(summary="s", checks={"passed": True}).checks == {"passed": True}
    assert QueryPlanOutput(intent="i", steps=[], visualizations=[], definitions="Definitions: x.").definitions


async def test_the_enhanced_report_goes_through_the_same_gate(monkeypatch):
    from backend.agents import enhanced_reporter as er

    gen = er.EnhancedReportGenerator.__new__(er.EnhancedReportGenerator)  # no real LLM client
    gen.llm = ScriptedLLM([BAD_NUMBER, BAD_NUMBER])

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(gen, "_gather_actor_activity", nothing)
    monkeypatch.setattr(gen, "_gather_event_storyline", nothing)
    out = await gen.generate_event_report(DATA, "Report.", include_storyline=False, include_gkg=False)
    assert out.checks["fallback"] and out.checks["failed"] == {"ungrounded_numbers": ["140"]}
    assert BAD_NUMBER not in out.summary and "100 articles" in out.summary
    assert out.to_dict()["checks"] == out.checks

    # Trends and dates outside the query are not checked here (the storyline and tone timeline
    # are time series); a grounded report with a trend word passes on the first draft.
    gen.llm = ScriptedLLM(["Coverage rose after 2024-01-05; the IOWA record drew 100 articles."])
    out = await gen.generate_event_report(DATA, "Report.", include_storyline=False, include_gkg=False)
    assert out.checks["passed"] and out.checks["attempts"] == 1
    assert out.checks["checked"] == ["ungrounded_numbers", "count_overclaims"]  # what the UI lists


def test_the_deep_dive_summary_is_not_cut_at_4000_characters():
    from backend.agents.enhanced_reporter import EnhancedReportGenerator

    gen = EnhancedReportGenerator.__new__(EnhancedReportGenerator)
    text = "\n".join(f"Paragraph {i}: " + "x" * 300 for i in range(20))  # about 6,300 characters
    summary, _ = gen._parse_report_text(text, max_chars=12000)
    assert summary == text and not summary.endswith("...")
    assert gen._parse_report_text(text, max_chars=4000)[0].endswith("...")
