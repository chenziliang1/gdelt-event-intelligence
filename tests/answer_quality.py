"""
Deterministic checks for the LLM-written report (/api/v1/analyze/report).

No LLM judge: each check is a rule over the report text and the data the report was given,
so a failure points at a specific sentence. What they catch:

* ungrounded numbers: a count, percentage or score in the report that is not in the data;
* sample-as-total: a total or a trend stated from a top-N event list (a sample, not a count);
* comparison direction: "increase" in the report when the computed direction is a decrease;
* dates outside the window: a date the query never covered;
* count over-claims: "four records, each with 70 articles" on a day with three such records.

Causes and motives are harder: the event records hold dates, places, actor labels, CAMEO codes,
tone and article counts, never why something happened, yet "which points to a legal dimension"
reads like a finding. ``causal_candidates`` is a recall-oriented rule that lists sentences with
causal or interpretive language, minus those that only state what the data cannot say; a Claude
judge (tests/causal_judge.py) decides which candidates are unsupported, and both are measured
against hand labels (tests/eval_runs/causal_labels/).

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


# A rounded bound on a value in the data: "more than 20,000" for 20,942, "63,000-plus" for 63,370.
_LOWER_BEFORE = re.compile(r"\b(?:more than|over|at least|upwards of|in excess of)\s+$", re.IGNORECASE)
_LOWER_AFTER = re.compile(r"^(?:-plus\b|\+|\s+or more\b)", re.IGNORECASE)
_UPPER_BEFORE = re.compile(r"\b(?:fewer than|less than|under|below|at most)\s+$", re.IGNORECASE)
_SPAN_AFTER = re.compile(r"^%?\s+(?:days?|weeks?|months?|years?|hours?)\b", re.IGNORECASE)
BOUND_SLACK = 0.10  # a bound counts only if the data value is within 10% of it


def report_numbers(text: str) -> List[Tuple[str, float, bool]]:
    """(raw, value, is_percent) for numbers that are not part of a date or year."""
    return [(raw, value, is_pct) for raw, value, is_pct, _ in _report_numbers(text)]


def _report_numbers(text: str) -> List[Tuple[str, float, bool, Optional[str]]]:
    """As report_numbers, plus "lower" / "upper" when the number is stated as a bound."""
    spans = _date_spans(text)
    found = []
    for m in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?", text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        raw = m.group(0)
        value = float(raw.rstrip("%").replace(",", ""))
        before, after = text[max(0, m.start() - 20):m.start()], text[m.end():m.end() + 10]
        bound = ("lower" if _LOWER_BEFORE.search(before) or _LOWER_AFTER.search(after)
                 else "upper" if _UPPER_BEFORE.search(before) else None)
        if bound and _SPAN_AFTER.search(text[m.end():m.end() + 10]):
            bound = None  # "over 31 days" is a span, not a bound
        found.append((raw, value, raw.endswith("%"), bound))
    return found


def _bounds(value: float, bound: Optional[str], candidates: Iterable[float]) -> bool:
    """A stated bound is grounded if a data value is on the right side of it and within the slack."""
    if bound == "lower":
        return any(value <= c <= value * (1 + BOUND_SLACK) for c in candidates)
    if bound == "upper":
        return any(value * (1 - BOUND_SLACK) <= c <= value for c in candidates)
    return False


def _close(value: float, candidates: Iterable[float]) -> bool:
    for c in candidates:
        if abs(value - c) <= max(0.051, abs(c) * 0.005):
            return True
    return False


def ungrounded_numbers(text: str, data: Any, ignore_below: float = 10) -> List[str]:
    """Numbers in the report that do not appear in the data (within rounding).

    A rounded bound ("more than 20,000", "63,000-plus", "under 500") is accepted when a data value
    lies on the stated side of it and within ``BOUND_SLACK``; a bare rounded number is not.
    Small numbers (counts like "three events", ranks) are ignored below ``ignore_below``;
    percentages are always checked. A percentage also matches a data value stored as a
    fraction (0.301 for 30.1%).
    """
    nums = data_numbers(data)
    bad = []
    for raw, value, is_pct, bound in _report_numbers(text):
        if not is_pct and abs(value) < ignore_below:
            continue
        candidates = {abs(c) for c in nums | ({n * 100 for n in nums} if is_pct else set())}
        # A bound is judged only as a bound: "more than 21,000" is wrong for 20,942 although the two
        # are within rounding.
        if not (_bounds(abs(value), bound, candidates) if bound else _close(abs(value), candidates)):
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
    for sentence in sentences(text):
        if _TREND_VERB.search(sentence) and _TREND_SUBJECT.search(sentence) and not _NOT_A_CLAIM.search(sentence):
            hits.append(sentence.strip())
    return hits


# ---------------------------------------------------------------------------
# Causes and motives
# ---------------------------------------------------------------------------

_CAUSAL = re.compile(
    r"\b(because|due to|owing to|thanks to|led to|lead(?:s|ing)? to|caus(?:e|es|ed|ing)|result(?:s|ed|ing)? in|"
    r"as a result|in response to|in reaction to|in retaliation|spark(?:s|ed|ing)?|trigger(?:s|ed|ing)?|"
    r"prompt(?:s|ed|ing)|driv(?:en|ing) by|drove|fu?el(?:l)?(?:ed|ing)|amid|stemm(?:ed|ing)|motivat\w*|"
    r"aimed at|in order to|in protest (?:of|against)|over (?:the )?(?:decision|ruling|policy|law|bill)|"
    r"suggest(?:s|ed|ing)?|point(?:s|ed|ing)? to|indicat(?:es|ed|ing)|signal(?:s|led|ing)|reflect(?:s|ed|ing)?|"
    r"consistent with|likely|probably|presumably|apparently|appears? to|seem(?:s|ed)? to|"
    r"tied to|linked to|connected to|related to|part of a (?:broader|wider|larger))\b",
    re.IGNORECASE,
)
# Sentences whose only interpretive content is a statement of what the data cannot show.
_DATA_LIMIT = re.compile(
    r"\b(does(?:n't| not) (?:say|show|state|name|establish|explain|tell|confirm|include)|"
    r"(?:can't|cannot|can not|could not|couldn't) (?:be )?(?:confirm|say|tell|establish|determin|show|know)\w*|"
    r"no (?:details?|information|indication|explanation) (?:on|of|about)|"
    r"(?:causes?|motives?|reasons?)(?: and \w+)? (?:are|is|remain|stay)s? (?:unknown|unclear|not (?:stated|given|recorded))|"
    r"nothing in (?:it|the data|the records?) (?:establishes|says|shows)|is not (?:in|stated in) the (?:data|records?))\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# "Canada vs. United States" never ends a sentence. After an initialism ("U.S.", "D.C.") the sentence
# ends only if the next word is capitalised: "in Washington, D.C. It drew" vs "U.S. political disputes".
_NEVER_ENDS = re.compile(r"\b(?:vs|Mr|Mrs|Ms|Dr|St|Gov|Sen|Rep|Gen|Lt|Col|Mt|Ft|e\.g|i\.e)\.$")
_INITIALISM = re.compile(r"(?:\b[A-Z]\.){2,}$")


def _ends_sentence(before: str, after: str) -> bool:
    if _NEVER_ENDS.search(before):
        return False
    if _INITIALISM.search(before):
        return after.lstrip("\"'(“‘")[:1].isupper()
    return True


def sentences(text: str) -> List[str]:
    text = text or ""
    out, start = [], 0
    for m in _SENTENCE_SPLIT.finditer(text):
        if "\n" not in m.group() and not _ends_sentence(text[:m.start()], text[m.end():]):
            continue
        out.append(text[start:m.start()].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [s for s in out if s]


def causal_candidates(text: str) -> List[str]:
    """Sentences that may state a cause, motive or connection the data does not contain.

    High recall by design: hedged inferences ("suggests a confrontation, though the details are not
    in the records") are kept, because a hedge does not make a guess grounded. Sentences that only
    say what the data cannot show are dropped; a limit clause that follows a claim ("suggests X,
    though the data doesn't confirm it") does not drop it. Not part of ``pass``: the judge decides.
    """
    out = []
    for s in sentences(text):
        limit = _DATA_LIMIT.search(s)
        if _CAUSAL.search(s[:limit.start()] if limit else s):
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Counts of records written as words
# ---------------------------------------------------------------------------

_COUNT_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_COUNT_CLAIM = re.compile(
    r"\b(?P<n>" + "|".join(_COUNT_WORDS) + r"|(?<![\d/.-])\d{1,2})\s+"
    r"(?:of the (?:\w+\s+){0,2}?|(?:[\w-]+\s+){0,2}?)(?:records|events|entries)\b",  # "five of the ten records"
    re.IGNORECASE)
# How many records of each step the report model is shown (ReportGenerator._format_data_for_report).
_SHOWN = {"similar_events": 8, "events": 10, "top_events": 10, "hot_events": 10}
# "a sample of ten records", "the top ten events", "of the five records" describe the list, not a subset.
_LIST_SIZE = re.compile(r"\b(?:of|the|top|first|these|all|only)\s*$", re.IGNORECASE)
# "70 articles", "97 and 95 articles", "114 to 120 articles" (a range).
# An article count binds the records only when it applies to each of them: "each with 70 articles",
# "90 articles each", "97 and 95 articles each" - not "with 250 articles each for the first three"
# or "each had 120 articles, except the third".
_NUM = r"\d[\d,]*(?:\s*(?:and|or|to|-|\u2013)\s*\d[\d,]*)?"
_NOT_ALL = r"(?!\s*,?\s*(?:except|but|for the first|apart from)\b)"
_ARTICLES_EACH = re.compile(
    rf"\beach\s+(?:[a-z]+\s+){{0,2}}?(?P<a>{_NUM})\s+articles\b{_NOT_ALL}"
    rf"|\b(?P<b>{_NUM})\s+articles\s+each\b(?!\s+for\b){_NOT_ALL}")
_PARENS = re.compile(r"\([^()]*\)")
_RANGE_JOIN = re.compile(r"^\s*(?:and|to|through|until|-|\u2013)\s*$")


def _article_ranges(s: str) -> List[Tuple[int, int]]:
    out = []
    for m in _ARTICLES_EACH.finditer(s):
        nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", m.group("a") or m.group("b"))]
        joined = re.search(r"\bto\b|-|\u2013", m.group("a") or m.group("b"))
        out += [(nums[0], nums[-1])] if joined and len(nums) == 2 else [(n, n) for n in nums]
    return out


def _date_ranges(s: str) -> List[Tuple[date, date]]:
    """Dates in a sentence, with "between X and Y", "from X to Y", "X to Y" and "December 23-29" as ranges."""
    found = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", s):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        found.append((m.start(), m.end(), d, d))
    for m in re.finditer(_DAY_RE, s):
        name = m.group("month") or m.group("month_after")
        month = next(i for i, full in enumerate(MONTHS, 1) if full.startswith(name.lower()[:3]))
        year = int(m.group("year") or m.group("year_after") or 2024)
        try:
            lo = date(year, month, int(m.group("day") or m.group("day_first")))
            hi = date(year, month, int(m.group("day2"))) if m.group("day2") else lo
        except ValueError:
            continue
        found.append((m.start(), m.end(), lo, hi))
    found.sort()
    out, i = [], 0
    while i < len(found):
        a0, a1, lo, hi = found[i]
        if i + 1 < len(found):
            b0, b1, lo2, hi2 = found[i + 1]
            gap, before = s[a1:b0], s[max(0, a0 - 9):a0]
            if _RANGE_JOIN.match(gap) and (re.search(r"\b(?:between|from)\s+$", before) or "and" not in gap):
                out.append((lo, hi2))
                i += 2
                continue
        out.append((lo, hi))
        i += 1
    return out


def _events(obj: Any, out: Dict[Any, Dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        if (obj.get("SQLDATE") or obj.get("date")) and ("NumArticles" in obj or "Actor1Name" in obj):
            key = obj.get("GlobalEventID") or obj.get("fingerprint") or id(obj)
            out.setdefault(key, obj)
        for v in obj.values():
            _events(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _events(v, out)


def _shown_events(data: Any) -> List[Dict[str, Any]]:
    """The records the report model saw: lists are cut to the formatter's caps. Data without step
    types (hand-written test data) is used whole."""
    found: Dict[Any, Dict[str, Any]] = {}
    if isinstance(data, dict) and all(isinstance(v, dict) and "type" in v for v in data.values()):
        for item in data.values():
            rows, kind = item.get("data"), item["type"]
            if kind in _SHOWN and isinstance(rows, list):
                rows = rows[:_SHOWN[kind]]
            elif kind == "regional_overview" and isinstance(rows, dict):
                rows = (rows.get("hot_events") or [])[:5]
            _events(rows, found)
    else:
        _events(data, found)
    return list(found.values())


def _iso(value: Any) -> str:
    s = str(value or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else s[:10]


def count_overclaims(text: str, data: Any) -> List[str]:
    """Sentences that say "N records/events" and name a date or an article count, when fewer than N
    records shown to the report model have that date and article count (a list of 50 related events
    is shown as its first 8, so "five records on April 24" is checked against those 8).

    The number check ignores small numbers and words, so "four records, each with 70 articles" on a
    day with three such records passed. Only over-claims are flagged: a sentence can name fewer
    records than match (a subset described by something the rule does not read). Two dates or two
    article counts named: either matches; "between X and Y" is a range. Article counts are used only
    when they apply to each record ("each with 70 articles"), and nothing inside parentheses is used. Places and actors are not used: names overlap ("Texas" is
    in most locations) and a sentence listing records in different places would never match.
    """
    events = _shown_events(data)
    if not events:
        return []
    hits = []
    for s in sentences(text):
        # Mask dates first, so the day in "January 6 three events" is neither a count nor
        # consumes the match that "three events" needs.
        masked = s
        for a, b in _date_spans(s):
            masked = masked[:a] + "#" * (b - a) + masked[b:]
        claims = [m for m in _COUNT_CLAIM.finditer(masked) if not _LIST_SIZE.search(masked[:m.start()])]
        if not claims:
            continue
        # A parenthesis describes one item of a list ("two are in Houston (2024-03-06 and 2024-03-12)"),
        # not the records counted, so its dates and counts are left out.
        outside = _PARENS.sub(" ", s)
        dates = _date_ranges(outside)
        ranges = _article_ranges(outside)
        if not (dates or ranges):
            continue

        def matches(e: Dict[str, Any]) -> bool:
            n = e.get("NumArticles") or e.get("num_articles")
            day = _iso(e.get("SQLDATE") or e.get("date"))
            return ((not dates or any(lo.isoformat() <= day <= hi.isoformat() for lo, hi in dates))
                    and (not ranges or (n is not None and any(lo <= int(n) <= hi for lo, hi in ranges))))

        available = sum(matches(e) for e in events)
        for m in claims:
            raw = m.group("n").lower()
            n = _COUNT_WORDS.get(raw, int(raw) if raw.isdigit() else 0)
            if n > available:
                hits.append(s)
                break
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
        "count_overclaims": count_overclaims(report_text, data),
        "causal_candidates": causal_candidates(report_text),  # informational; see causal_judge.py
    }
    result["pass"] = (
        not result["ungrounded_numbers"]
        and not result["sample_as_total"]
        and not result["qualitative_trend"]
        and result["comparison_direction_ok"] is not False
        and not result["dates_outside_window"]
        and not result["count_overclaims"]
    )
    return result


def report_text(report: Dict[str, Any]) -> str:
    return "\n".join([report.get("summary") or "", *(report.get("key_findings") or [])])


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
