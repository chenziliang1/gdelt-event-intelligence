"""
Deterministic date helpers for the planner and the query layer.

Why this exists
---------------
Date ranges used to come from three places that disagreed with each other:
the local LLM router (which was never given a reference date), regex
fallbacks (month ends hard-coded to the 28th or the 31st), and
``parse_time_hint`` (anchored to 2024-01-31). This module is the single
place that turns a phrase into a concrete, validated date range.

Everything here is pure Python with no I/O so it can be unit tested.

Anchor date
-----------
The dataset is a static 2024 extract, so "this month" or "yesterday" have to
be resolved relative to the *dataset*, not the wall clock. The anchor is the
last day of the dataset (2024-12-31) unless ``GDELT_REFERENCE_DATE`` is set.
"""

from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

DATA_YEAR = 2024
DATA_START = date(DATA_YEAR, 1, 1)
DATA_END = date(DATA_YEAR, 12, 31)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_NUMBER_WORDS = {
    "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fourteen": 14,
}


# ---------------------------------------------------------------------------
# Anchor
# ---------------------------------------------------------------------------

def reference_date() -> date:
    """The date that relative phrases ("this month") are resolved against."""
    raw = os.getenv("GDELT_REFERENCE_DATE")
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    return DATA_END


# ---------------------------------------------------------------------------
# Calendar arithmetic
# ---------------------------------------------------------------------------

def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of a month, computed from the calendar.

    This replaces the old hard-coded ``-28`` / ``-31`` month ends.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def week_bounds(day: date) -> tuple[date, date]:
    """Monday to Sunday of the calendar week containing ``day``."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def iso(day: date) -> str:
    return day.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DateRange:
    start: date
    end: date
    label: str
    # "day" | "week" | "month" | "year" | "days" | "quarter" | "explicit"
    kind: str = "explicit"

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_iso(self) -> str:
        return iso(self.start)

    @property
    def end_iso(self) -> str:
        return iso(self.end)


@dataclass
class RangeValidation:
    """Outcome of checking a date range. ``status`` replaces the old
    unconditional ``confidence="high"``."""

    status: str  # "valid" | "invalid"
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "valid"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _parse_iso(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def validate_range(
    start: Optional[str],
    end: Optional[str],
    expected_kind: Optional[str] = None,
) -> RangeValidation:
    """Check that a range is real, ordered, inside the dataset, and complete.

    ``expected_kind="month"`` additionally requires the range to cover the
    whole calendar month it starts in. That is the check that would have
    caught "March 1 to March 28": every date is valid, but it is not March.
    """
    problems: List[str] = []
    d1, d2 = _parse_iso(start), _parse_iso(end)
    if d1 is None:
        problems.append(f"start date {start!r} is not a valid YYYY-MM-DD date")
    if d2 is None:
        problems.append(f"end date {end!r} is not a valid YYYY-MM-DD date")
    if d1 and d2:
        if d1 > d2:
            problems.append("start date is after end date")
        if d2 < DATA_START or d1 > DATA_END:
            problems.append(f"range is outside the dataset ({iso(DATA_START)} to {iso(DATA_END)})")
        elif d1 < DATA_START or d2 > DATA_END:
            # "this week" anchored on Tuesday 2024-12-31 runs to 2025-01-05; use clip_to_dataset.
            problems.append(f"range extends beyond the dataset ({iso(DATA_START)} to {iso(DATA_END)})")
        if expected_kind == "month":
            first, last = month_bounds(d1.year, d1.month)
            if d1 != first or d2 != last:
                problems.append(
                    f"range does not cover the whole month ({iso(first)} to {iso(last)})"
                )
    return RangeValidation("invalid" if problems else "valid", problems)


# ---------------------------------------------------------------------------
# Phrase -> range
# ---------------------------------------------------------------------------

def _month_number(token: str) -> int:
    return MONTHS[token.lower()[:3]]


_AMBIGUOUS_MONTHS = {"may", "march"}
_MONTH_PREP = r"(?:in|during|for|of|since|from|to|through|until|and|with|than|vs\.?|versus|between|early|late|mid)"


def _month_token_ok(text: str, match: "re.Match[str]") -> bool:
    """"may" and "march" are also ordinary words ("may increase", "a protest march").

    Accept them as months only with an explicit year, after a month-ish
    preposition, at the start of the text, or right before a comparison word.
    """
    token = match.group(1).lower()
    if token not in _AMBIGUOUS_MONTHS:
        return True
    if match.group(2):
        return True
    before = text[: match.start()]
    after = text[match.end():]
    if re.search(rf"\b{_MONTH_PREP}\s+$", before, re.IGNORECASE):
        return True
    if not before.strip():
        return True
    return bool(re.match(r"\s*(?:compared|vs\.?|versus|than)\b", after, re.IGNORECASE))


def parse_month_range(text: str, default_year: int = DATA_YEAR) -> Optional[DateRange]:
    """"March 2024", "in march", "feb" -> the whole calendar month."""
    for m in re.finditer(rf"\b({_MONTH_NAMES})\b(?:\s*,?\s*(\d{{4}}))?", text, re.IGNORECASE):
        if not _month_token_ok(text, m):
            continue
        year = int(m.group(2)) if m.group(2) else default_year
        month = _month_number(m.group(1))
        start, end = month_bounds(year, month)
        return DateRange(start, end, f"{calendar.month_name[month]} {year}", "month")
    return None


def parse_relative_range(text: str, ref: Optional[date] = None) -> Optional[DateRange]:
    """Resolve phrases like "last week" or "past 3 days" against ``ref``.

    Conventions (written down so they are testable):
      * today / yesterday: the single day.
      * this week / last week: calendar weeks, Monday to Sunday.
      * this month / last month: calendar months, always whole.
      * this year / last year: calendar years.
      * past N days / weeks: the N*unit days ending on ``ref``.
      * recently: the 30 days ending on ``ref``.
    """
    ref = ref or reference_date()
    t = text.lower()

    if re.search(r"\byesterday\b", t):
        d = ref - timedelta(days=1)
        return DateRange(d, d, "yesterday", "day")
    if re.search(r"\btoday\b|\bright now\b", t):
        return DateRange(ref, ref, "today", "day")

    m = re.search(r"\b(?:past|last|previous)\s+(\d+|a|one|two|three|four|five|six|seven|eight|nine|ten|fourteen)\s+(day|week|month)s?\b", t)
    if m:
        raw, unit = m.group(1), m.group(2)
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        if unit == "month":
            days = n * 30
        elif unit == "week":
            days = n * 7
        else:
            days = n
        start = ref - timedelta(days=days - 1)
        return DateRange(start, ref, f"past {n} {unit}{'s' if n != 1 else ''}", "days")

    if re.search(r"\bthis\s+week\b", t):
        s, e = week_bounds(ref)
        return DateRange(s, e, "this week", "week")
    if re.search(r"\b(?:last|previous)\s+week\b", t):
        s, e = week_bounds(ref - timedelta(days=7))
        return DateRange(s, e, "last week", "week")
    if re.search(r"\bthis\s+month\b", t):
        s, e = month_bounds(ref.year, ref.month)
        return DateRange(s, e, "this month", "month")
    if re.search(r"\b(?:last|previous)\s+month\b", t):
        y, mo = previous_month(ref.year, ref.month)
        s, e = month_bounds(y, mo)
        return DateRange(s, e, "last month", "month")
    if re.search(r"\bthis\s+year\b", t):
        return DateRange(date(ref.year, 1, 1), date(ref.year, 12, 31), "this year", "year")
    if re.search(r"\b(?:last|previous)\s+year\b", t):
        return DateRange(date(ref.year - 1, 1, 1), date(ref.year - 1, 12, 31), "last year", "year")
    if re.search(r"\brecent(?:ly)?\b", t):
        return DateRange(ref - timedelta(days=29), ref, "recently (last 30 days)", "days")
    return None


def parse_quarter_range(text: str, default_year: int = DATA_YEAR) -> Optional[DateRange]:
    m = re.search(r"\bq([1-4])(?:\s+(\d{4}))?\b", text, re.IGNORECASE)
    if not m:
        return None
    q = int(m.group(1))
    year = int(m.group(2)) if m.group(2) else default_year
    start, _ = month_bounds(year, 3 * (q - 1) + 1)
    _, end = month_bounds(year, 3 * q)
    return DateRange(start, end, f"Q{q} {year}", "quarter")


# "Jan 9, 2024", "January 9 2024", and without a year "Dec 3rd", "July 4" (held-out v3: both were
# read as the whole month). The day must not be followed by more digits ("June 30 2024" keeps its
# year; "March 2024" has no day).
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}})(st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b(?!\s*[-/]?\d)", re.IGNORECASE)
_US_DAY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")  # 3/15/2024
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE)  # 4 July
_ISO_RANGE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})\s*(?:to|through|until|-|–)\s*(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)


def _month_day_matches(text: str):
    """(match, year, month, day) for month-name days; "may 5"/"march 3" need a year, an ordinal or a
    month-ish preposition before them, like a bare "may"/"march" (``_month_token_ok``)."""
    for m in _MONTH_DAY.finditer(text):
        token = m.group(1).lower()
        if token in _AMBIGUOUS_MONTHS and not (m.group(3) or m.group(4)):
            before = text[: m.start()]
            if not (re.search(rf"\b(?:on|{_MONTH_PREP})\s+$", before, re.IGNORECASE) or not before.strip()):
                continue
        yield m, int(m.group(4) or DATA_YEAR), _month_number(m.group(1)), int(m.group(2))
    for m in _DAY_MONTH.finditer(text):  # "4 July", "1st of May 2024"; not "the top 5 March events"
        if re.search(r"\b(?:top|past|last|first|next)\s+$", text[: m.start()], re.IGNORECASE):
            continue
        yield m, int(m.group(3) or DATA_YEAR), _month_number(m.group(2)), int(m.group(1))


def parse_explicit_range(text: str) -> Optional[DateRange]:
    """"from 2024-02-01 to 2024-02-15" -> that range (it used to become its first day)."""
    m = _ISO_RANGE.search(text)
    if m:
        d1, d2 = _parse_iso(m.group(1)), _parse_iso(m.group(2))
        if d1 and d2 and d1 <= d2:
            return DateRange(d1, d2, f"{iso(d1)} to {iso(d2)}", "explicit")
    return None


def parse_month_span(text: str, default_year: int = DATA_YEAR) -> Optional[DateRange]:
    """"between April and June", "from April to June", "April through June" -> April 1 to June 30."""
    m = re.search(
        rf"\b(?:between|from)?\s*({_MONTH_NAMES})\b(?:\s+(\d{{4}}))?\s+(?:and|to|through|until|-|–)\s+"
        rf"({_MONTH_NAMES})\b(?:\s+(\d{{4}}))?", text, re.IGNORECASE)
    if not m or not (m.group(0).lower().lstrip().startswith(("between", "from")) or
                     re.search(r"\b(?:to|through|until)\b|[-–]", m.group(0), re.IGNORECASE)):
        return None
    y2 = int(m.group(4) or m.group(2) or default_year)
    y1 = int(m.group(2) or y2)
    start, _ = month_bounds(y1, _month_number(m.group(1)))
    _, end = month_bounds(y2, _month_number(m.group(3)))
    if start >= end:
        return None
    return DateRange(start, end, f"{calendar.month_name[start.month]} to {calendar.month_name[end.month]} {y2}", "explicit")


def parse_explicit_day(text: str) -> Optional[DateRange]:
    """"2024-01-09", "January 9 2024", "Jan 9, 2024", "Dec 3rd", "July 4", "3/15/2024" -> that day.

    A month and day without a year is in the dataset year.
    """
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        d = _parse_iso(m.group(0))
        if d:
            return DateRange(d, d, iso(d), "day")
    for m, year, month, day in _month_day_matches(text):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return DateRange(d, d, iso(d), "day")
    m = _US_DAY.search(text)
    if m:
        try:
            d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        return DateRange(d, d, iso(d), "day")
    return None


def clip_to_dataset(r: DateRange) -> Optional[DateRange]:
    """The part of ``r`` inside the dataset, or None if they do not overlap.

    Returns ``r`` itself when nothing was cut, so callers can tell (``is``) whether to say so.
    """
    start, end = max(r.start, DATA_START), min(r.end, DATA_END)
    if start > end:
        return None
    if (start, end) == (r.start, r.end):
        return r
    return DateRange(start, end, r.label, r.kind)


def find_impossible_day(text: str) -> Optional[str]:
    """The first explicit day in ``text`` that is not a real calendar date ("2024-02-30").

    ``parse_explicit_day`` skips such a date, which used to let the router quietly
    substitute a nearby real one; the caller uses this to tell the user instead.
    """
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        if _parse_iso(m.group(0)) is None:
            return m.group(0)
    for m, year, month, day in _month_day_matches(text):  # also "February 30" with no year
        try:
            date(year, month, day)
        except ValueError:
            return m.group(0)
    for m in _US_DAY.finditer(text):
        try:
            date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return m.group(0)
    return None


def resolve_dates(text: str, ref: Optional[date] = None) -> Optional[DateRange]:
    """Resolve the single most specific date expression in ``text``.

    Precedence: explicit range, explicit day, quarter, relative phrase, month span, month name.
    Returns ``None`` when the text has no date expression at all (the caller then decides whether
    to default or to ask).
    """
    return (
        parse_explicit_range(text)
        or parse_explicit_day(text)
        or parse_quarter_range(text)
        or parse_relative_range(text, ref)
        or parse_month_span(text)
        or parse_month_range(text)
    )


# ---------------------------------------------------------------------------
# Period comparison
# ---------------------------------------------------------------------------

_COMPARE_STRONG = re.compile(
    r"\b(compar(?:e|ed|ing|ison)|versus|vs\.?|than|increase[ds]?|decrease[ds]?|"
    r"rise[ns]?|rose|fall(?:s|en)?|fell|drop(?:ped|s)?|grow(?:th|n|s)?|grew|change[ds]?|"
    r"declin(?:e|es|ed|ing)|fewer|spike[ds]?|surge[ds]?|"
    r"(?:go|goes|going|gone|went)\s+(?:up|down))\b",
    re.IGNORECASE,
)


def previous_period(current: DateRange) -> DateRange:
    """The period of the same kind immediately before ``current``."""
    if current.kind == "month":
        y, mo = previous_month(current.start.year, current.start.month)
        s, e = month_bounds(y, mo)
        return DateRange(s, e, f"{calendar.month_name[mo]} {y}", "month")
    if current.kind == "week":
        s = current.start - timedelta(days=7)
        return DateRange(s, s + timedelta(days=6), f"the week before ({iso(s)})", "week")
    if current.kind == "quarter":
        q = (current.start.month - 1) // 3 + 1
        year = current.start.year
        q, year = (4, year - 1) if q == 1 else (q - 1, year)
        s, _ = month_bounds(year, 3 * (q - 1) + 1)
        _, e = month_bounds(year, 3 * q)
        return DateRange(s, e, f"Q{q} {year}", "quarter")
    if current.kind == "year":
        y = current.start.year - 1
        return DateRange(date(y, 1, 1), date(y, 12, 31), str(y), "year")
    length = current.days
    end = current.start - timedelta(days=1)
    start = end - timedelta(days=length - 1)
    return DateRange(start, end, f"the {length} days before {iso(current.start)}", current.kind)


def resolve_comparison_periods(
    text: str, ref: Optional[date] = None
) -> Optional[tuple[DateRange, DateRange]]:
    """Return ``(earlier, later)`` if ``text`` asks for a period comparison.

    Handles three shapes:
      1. two explicit periods: "March compared with February", "Q2 vs Q1";
      2. a comparison word plus one period: "did protests increase this
         month" compares this month with the one before it;
      3. otherwise ``None``: not a comparison question.

    When two periods are named, the earlier one is returned first regardless
    of the order in the sentence, and the sentence order is not trusted for
    which is "current".
    """
    if not _COMPARE_STRONG.search(text):
        return None

    explicit = [r for r in (parse_explicit_range(m.group(0)) for m in _ISO_RANGE.finditer(text)) if r]
    if len(explicit) >= 2:  # "from 2024-02-01 to 2024-02-15 versus 2024-03-01 to 2024-03-15"
        pair = sorted(explicit[:2], key=lambda r: r.start)
        return pair[0], pair[1]

    months = _all_month_ranges(text)
    if len(months) >= 2:
        pair = sorted(months[:2], key=lambda r: r.start)
        return pair[0], pair[1]

    quarters = [
        parse_quarter_range(m.group(0))
        for m in re.finditer(r"\bq[1-4](?:\s+\d{4})?\b", text, re.IGNORECASE)
    ]
    quarters = [q for q in quarters if q]
    if len(quarters) >= 2:
        pair = sorted(quarters[:2], key=lambda r: r.start)
        return pair[0], pair[1]

    single = resolve_dates(text, ref)
    if single is None:
        return None
    if single.kind in ("day",):
        return None  # a single day has no natural "previous period" to compare
    prior = previous_period(single)
    return prior, single


def _all_month_ranges(text: str) -> List[DateRange]:
    ranges: List[DateRange] = []
    for m in re.finditer(rf"\b({_MONTH_NAMES})\b(?:\s*,?\s*(\d{{4}}))?", text, re.IGNORECASE):
        if not _month_token_ok(text, m):
            continue
        year = int(m.group(2)) if m.group(2) else DATA_YEAR
        month = _month_number(m.group(1))
        start, end = month_bounds(year, month)
        ranges.append(DateRange(start, end, f"{calendar.month_name[month]} {year}", "month"))
    return ranges
