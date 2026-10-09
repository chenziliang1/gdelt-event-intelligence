"""
Deterministic checks for the LLM-written report (/api/v1/analyze/report).

No LLM judge: each check is a rule over the report text and the data the report was given,
so a failure points at a specific sentence. What they catch:

* ungrounded numbers: a count, percentage or score in the report that is not in the data;
* sample-as-total: a total or a trend stated from a top-N event list (a sample, not a count);
* comparison direction: "increase" in the report when the computed direction is a decrease;
* dates outside the window: a date the query never covered.

Used by tests/run_answer_quality_eval.py; unit tested in tests/test_answer_quality.py.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

MONTHS = ("january february march april may june july august september october november december").split()
_MONTH_RE = "|".join(m.capitalize() for m in MONTHS) + "|" + "|".join(m[:3].capitalize() for m in MONTHS)

SAMPLE_STEPS = {"events", "top_events", "hot_events", "similar_events"}
COUNT_STEPS = {"compare_periods", "daily_brief", "regional_overview"}

DIRECTION_WORDS = {
    "increase": r"\b(increas\w*|rose|rise\w*|up\b|higher|grew|growth|more)\b",
    "decrease": r"\b(decreas\w*|fell|fall\w*|drop\w*|down\b|lower|declin\w*|fewer|less)\b",
    "flat": r"\b(flat|stable|steady|little change|roughly (the )?same|about the same|unchanged)\b",
    "inconclusive_low_volume": r"\b(too few|insufficient|low volume|not enough|small number|cannot|can't)\b",
}


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

def _data_numbers(obj: Any, out: Set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in re.finditer(r"-?\d+(?:\.\d+)?", obj.replace(",", "")):
            out.add(float(m.group(0)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _data_numbers(v, out)
    elif isinstance(obj, list):
        out.add(float(len(obj)))
        for v in obj:
            _data_numbers(v, out)


def data_numbers(data: Any) -> Set[float]:
    out: Set[float] = set()
    _data_numbers(data, out)
    return out


# "January 9", "Jan 9, 2024", "December 23-29, 2024" (a range), "30 December 2024".
# The day must not run into more digits: "December 2024" is a month, not "December 20".
_DAY_RE = (
    rf"\b(?P<month>{_MONTH_RE})\.?\s+(?P<day>\d{{1,2}})(?!\d)(?:st|nd|rd|th)?"
    rf"(?:\s*[-\u2013]\s*(?P<day2>\d{{1,2}})(?!\d))?(?:,?\s+(?P<year>\d{{4}}))?"
    rf"|\b(?P<day_first>\d{{1,2}})\s+(?P<month_after>{_MONTH_RE})\b(?:\s+(?P<year_after>\d{{4}}))?"
)


def _date_spans(text: str) -> List[Tuple[int, int]]:
    spans = [m.span() for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text)]
    spans += [m.span() for m in re.finditer(_DAY_RE, text)]
    spans += [m.span() for m in re.finditer(r"\b(?:19|20)\d{2}\b", text)]  # years
    return spans


def report_numbers(text: str) -> List[Tuple[str, float, bool]]:
    """(raw, value, is_percent) for numbers that are not part of a date or year."""
    spans = _date_spans(text)
    found = []
    for m in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?", text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        raw = m.group(0)
        value = float(raw.rstrip("%").replace(",", ""))
        found.append((raw, value, raw.endswith("%")))
    return found


def _close(value: float, candidates: Iterable[float]) -> bool:
    for c in candidates:
        if abs(value - c) <= max(0.051, abs(c) * 0.005):
            return True
    return False


def ungrounded_numbers(text: str, data: Any, ignore_below: float = 10) -> List[str]:
    """Numbers in the report that do not appear in the data (within rounding).

    Small numbers (counts like "three events", ranks) are ignored below ``ignore_below``;
    percentages are always checked. A percentage also matches a data value stored as a
    fraction (0.301 for 30.1%).
    """
    nums = data_numbers(data)
    bad = []
    for raw, value, is_pct in report_numbers(text):
        if not is_pct and abs(value) < ignore_below:
            continue
        candidates = nums | ({n * 100 for n in nums} if is_pct else set())
        if not _close(abs(value), {abs(c) for c in candidates}):
            bad.append(raw)
    return bad


# ---------------------------------------------------------------------------
# Totals and trends from a sample
# ---------------------------------------------------------------------------

_TOTAL_CLAIM = re.compile(
    r"\b(a total of|in total|totall?ed|altogether|overall,? there (?:were|was)|"
    r"(?:there|which) were \d[\d,]* (?:events|incidents|protests))\b", re.IGNORECASE)
_TREND_CLAIM = re.compile(
    r"\b(increas\w*|decreas\w*|rose|fell|declin\w*|surg\w*|spik\w*|jump\w*|dropp?\w*)\b[^.]{0,40}?\d+(?:\.\d+)?%",
    re.IGNORECASE)


def sample_as_total(text: str, step_types: Iterable[str]) -> List[str]:
    """Total/trend claims when the data is only a top-N sample (no count step)."""
    steps = set(step_types)
    if not steps & SAMPLE_STEPS or steps & COUNT_STEPS:
        return []
    return [m.group(0) for m in _TOTAL_CLAIM.finditer(text)] + [m.group(0) for m in _TREND_CLAIM.finditer(text)]


_TREND_VERB = re.compile(
    r"\b(rose|rising|increas(?:ed|ing)|grew|growing|climb(?:ed|ing)|picked up|ramp(?:ed|ing) up|"
    r"fell|falling|declin(?:ed|ing)|dropp(?:ed|ing)|decreas(?:ed|ing)|waned|tapered|surg(?:ed|ing)|spik(?:ed|ing)|"
    r"intensif(?:ied|ying)|escalat(?:ed|ing))\b", re.IGNORECASE)
_TREND_SUBJECT = re.compile(r"\b(activity|coverage|protests?|events|reporting|attention|incidents|tensions?|volume)\b", re.IGNORECASE)
_NOT_A_CLAIM = re.compile(
    r"\b(whether|cannot|can't|can not|could not|couldn't|not (?:possible|able)|no period comparison|"
    r"(?:does|do|did) not (?:show|say|tell|allow)|doesn't|unclear|impossible)\b|\bfell on\b", re.IGNORECASE)


def qualitative_trend(text: str, step_types: Iterable[str]) -> List[str]:
    """Sentences claiming change over time, with or without a number, from data that has no
    time series (a top-N sample). "Coverage rose toward the end of the month" was the case the
    numeric check missed. Negated or hedged sentences ("can't say whether activity rose") and
    "fell on <date>" are not claims."""
    steps = set(step_types)
    if not steps & SAMPLE_STEPS or "compare_periods" in steps:
        return []
    hits = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _TREND_VERB.search(sentence) and _TREND_SUBJECT.search(sentence) and not _NOT_A_CLAIM.search(sentence):
            hits.append(sentence.strip())
    return hits


# ---------------------------------------------------------------------------
# Comparison direction
# ---------------------------------------------------------------------------

def comparison_direction_ok(text: str, comparison: Optional[Dict[str, Any]]) -> Optional[bool]:
    """None when there is no comparison; else whether the report states the computed direction."""
    if not comparison:
        return None
    direction = comparison.get("direction")
    pattern = DIRECTION_WORDS.get(direction)
    if not pattern:
        return None  # no_data / new_activity: no direction word to require
    return re.search(pattern, text, re.IGNORECASE) is not None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def _report_dates(text: str, default_year: int = 2024) -> List[Tuple[str, date]]:
    out = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        try:
            out.append((m.group(0), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except ValueError:
            out.append((m.group(0), date.max))
    for m in re.finditer(_DAY_RE, text):
        name = m.group("month") or m.group("month_after")
        month = next(i for i, full in enumerate(MONTHS, 1) if full.startswith(name.lower()[:3]))
        year = int(m.group("year") or m.group("year_after") or default_year)
        days = [m.group("day") or m.group("day_first")] + ([m.group("day2")] if m.group("day2") else [])
        for day in days:
            try:
                out.append((m.group(0), date(year, month, int(day))))
            except ValueError:
                out.append((m.group(0), date.max))
    return out


def dates_outside(text: str, start: Optional[str], end: Optional[str]) -> List[str]:
    if not start or not end:
        return []
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    return [raw for raw, d in _report_dates(text) if not (lo <= d <= hi)]


# ---------------------------------------------------------------------------
# One item
# ---------------------------------------------------------------------------

def plan_window(plan: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    starts, ends = [], []
    for step in plan.get("steps", []):
        p = step.get("params", {})
        if p.get("start_date") and p.get("end_date"):
            starts.append(p["start_date"]); ends.append(p["end_date"])
        elif p.get("query_date"):
            starts.append(p["query_date"]); ends.append(p["query_date"])
        for period in p.get("periods") or []:
            starts.append(period["start"]); ends.append(period["end"])
    return (min(starts), max(ends)) if starts else (None, None)


def check_report(report_text: str, plan: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    step_types = [s.get("type") for s in plan.get("steps", [])]
    comparison = next(
        (v.get("data") for k, v in data.items() if k.startswith("compare_periods") and isinstance(v, dict)), None)
    start, end = plan_window(plan)
    # Event-detail and similar-event lookups can legitimately mention other dates.
    window_applies = not ({"event_detail", "similar_events"} & set(step_types))
    result = {
        "ungrounded_numbers": ungrounded_numbers(report_text, data),
        "sample_as_total": sample_as_total(report_text, step_types),
        "qualitative_trend": qualitative_trend(report_text, step_types),
        "comparison_direction_ok": comparison_direction_ok(report_text, comparison),
        "dates_outside_window": dates_outside(report_text, start, end) if window_applies else [],
    }
    result["pass"] = (
        not result["ungrounded_numbers"]
        and not result["sample_as_total"]
        and not result["qualitative_trend"]
        and result["comparison_direction_ok"] is not False
        and not result["dates_outside_window"]
    )
    return result


def report_text(report: Dict[str, Any]) -> str:
    return "\n".join([report.get("summary") or "", *(report.get("key_findings") or [])])


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
