"""Date handling. The first test class is the regression for the March 28 bug."""
from datetime import date

import pytest

from backend.queries.date_utils import (
    clip_to_dataset,
    find_impossible_day,
    month_bounds,
    parse_month_range,
    parse_relative_range,
    previous_period,
    resolve_comparison_periods,
    resolve_dates,
    validate_range,
)

REF = date(2024, 12, 31)


class TestMonthBounds:
    @pytest.mark.parametrize(
        "year,month,last_day",
        [
            (2024, 1, 31),
            (2024, 2, 29),   # leap year
            (2023, 2, 28),   # non-leap year
            (2024, 3, 31),   # the month that used to end on the 28th
            (2024, 4, 30),   # a 30-day month
            (2024, 12, 31),
        ],
    )
    def test_last_day_comes_from_the_calendar(self, year, month, last_day):
        start, end = month_bounds(year, month)
        assert start == date(year, month, 1)
        assert end == date(year, month, last_day)

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            month_bounds(2024, 13)


class TestMarchRegression:
    def test_month_only_phrase_covers_the_whole_month(self):
        r = parse_month_range("what happened in march 2024")
        assert (r.start_iso, r.end_iso) == ("2024-03-01", "2024-03-31")

    def test_validator_rejects_march_1_to_28(self):
        # Every date is valid, but it is not "all of March".
        v = validate_range("2024-03-01", "2024-03-28", expected_kind="month")
        assert not v.ok
        assert any("whole month" in p for p in v.problems)

    def test_validator_accepts_full_month(self):
        assert validate_range("2024-03-01", "2024-03-31", expected_kind="month").ok


class TestValidateRange:
    def test_rejects_reversed_range(self):
        assert not validate_range("2024-05-10", "2024-05-01").ok

    def test_rejects_non_dates(self):
        assert not validate_range("2024-02-30", "2024-03-01").ok
        assert not validate_range("not a date", "2024-03-01").ok

    def test_rejects_range_outside_dataset(self):
        assert not validate_range("2026-07-13", "2026-07-13").ok

    def test_rejects_range_that_runs_past_the_dataset(self):
        # "this week" on 2024-12-31 used to pass as valid and query into 2025.
        v = validate_range("2024-12-30", "2025-01-05")
        assert not v.ok and "beyond the dataset" in v.problems[0]

    def test_clip_to_dataset(self):
        week = parse_relative_range("this week", REF)
        clipped = clip_to_dataset(week)
        assert (clipped.start_iso, clipped.end_iso) == ("2024-12-30", "2024-12-31")
        inside = parse_relative_range("last week", REF)
        assert clip_to_dataset(inside) is inside


class TestRelativeRanges:
    def test_yesterday_and_today(self):
        assert parse_relative_range("what happened yesterday", REF).start == date(2024, 12, 30)
        assert parse_relative_range("events today", REF).start == REF

    def test_last_week_is_the_previous_monday_to_sunday(self):
        # 2024-12-31 is a Tuesday; the week before is Dec 23 to Dec 29.
        r = parse_relative_range("protests in Texas last week", REF)
        assert (r.start_iso, r.end_iso) == ("2024-12-23", "2024-12-29")
        assert r.days == 7  # the old planner returned a 131-day window here

    def test_past_n_days_has_exactly_n_days(self):
        r = parse_relative_range("events in the past 3 days in New York", REF)
        assert r.days == 3 and r.end == REF  # old planner: 138 days

    def test_this_and_last_month_are_whole_months(self):
        assert parse_relative_range("this month", REF).end_iso == "2024-12-31"
        last = parse_relative_range("last month", REF)
        assert (last.start_iso, last.end_iso) == ("2024-11-01", "2024-11-30")

    def test_last_month_across_a_year_boundary(self):
        r = parse_relative_range("last month", date(2024, 1, 15))
        assert (r.start_iso, r.end_iso) == ("2023-12-01", "2023-12-31")

    def test_anchor_is_not_the_wall_clock(self, monkeypatch):
        monkeypatch.delenv("GDELT_REFERENCE_DATE", raising=False)
        assert parse_relative_range("this month").start.year == 2024

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("GDELT_REFERENCE_DATE", "2024-06-15")
        r = parse_relative_range("this month")
        assert (r.start_iso, r.end_iso) == ("2024-06-01", "2024-06-30")


class TestAmbiguousWords:
    def test_protest_march_is_not_the_month(self):
        assert resolve_dates("show me a protest march in Texas") is None

    def test_may_the_verb_is_not_the_month(self):
        assert resolve_dates("protests may increase in Canada") is None

    def test_march_the_month_is_recognised(self):
        assert resolve_dates("protests in march").start_iso == "2024-03-01"
        assert resolve_dates("March 2024 events").end_iso == "2024-03-31"
        assert resolve_dates("in May").start_iso == "2024-05-01"


class TestExplicitDays:
    def test_iso_and_written_dates(self):
        assert resolve_dates("hot events on 2024-01-09").start_iso == "2024-01-09"
        assert resolve_dates("summarize January 9 2024 for me").start_iso == "2024-01-09"

    def test_impossible_date_is_not_resolved_as_a_day(self):
        assert resolve_dates("on 2024-02-30") is None

    def test_impossible_date_is_reported(self):
        assert find_impossible_day("what happened in Texas on 2024-02-30") == "2024-02-30"
        assert find_impossible_day("events on February 30, 2024") == "February 30, 2024"
        assert find_impossible_day("hot events on 2024-02-29") is None  # leap day is real
        assert find_impossible_day("protests in Texas last week") is None


class TestComparison:
    def test_two_named_months_are_ordered_regardless_of_sentence_order(self):
        a, b = resolve_comparison_periods("Did protests increase in Canada in March compared with February?")
        assert (a.start_iso, a.end_iso) == ("2024-02-01", "2024-02-29")
        assert (b.start_iso, b.end_iso) == ("2024-03-01", "2024-03-31")
        a2, b2 = resolve_comparison_periods("February vs March protests")
        assert (a2.start, b2.start) == (a.start, b.start)

    def test_single_period_compares_with_the_one_before(self):
        a, b = resolve_comparison_periods("Did protest activity increase in Canada this month?", REF)
        assert (a.start_iso, a.end_iso) == ("2024-11-01", "2024-11-30")
        assert (b.start_iso, b.end_iso) == ("2024-12-01", "2024-12-31")

    def test_quarters(self):
        a, b = resolve_comparison_periods("Q2 vs Q1")
        assert a.start_iso == "2024-01-01" and b.end_iso == "2024-06-30"

    def test_non_comparison_questions_return_none(self):
        assert resolve_comparison_periods("show me protests in Texas last week", REF) is None
        assert resolve_comparison_periods("what happened in march", REF) is None

    def test_single_day_has_no_previous_period(self):
        assert resolve_comparison_periods("did it change yesterday", REF) is None

    def test_previous_period_for_january_wraps_the_year(self):
        jan = parse_month_range("january 2024")
        prev = previous_period(jan)
        assert (prev.start_iso, prev.end_iso) == ("2023-12-01", "2023-12-31")


# --- held-out v3 (2026-10-09): days without a year, month spans, explicit ranges ------------

@pytest.mark.parametrize("text,expected", [
    ("Give me a recap of what happened on Dec 3rd", ("2024-12-03", "2024-12-03")),  # was the whole of December
    ("Could I get a digest of the news from July 4?", ("2024-07-04", "2024-07-04")),  # was the whole of July
    ("the march on Washington on 4 July", ("2024-07-04", "2024-07-04")),
    ("What happened on 3/15/2024?", ("2024-03-15", "2024-03-15")),
    ("Give me an overview of Québec between April and June", ("2024-04-01", "2024-06-30")),  # was April
    ("protests from 2024-02-01 to 2024-02-15", ("2024-02-01", "2024-02-15")),  # was its first day
    ("in the top 5 June events", ("2024-06-01", "2024-06-30")),  # "5 June" here is not a day
    ("violence may 3 escalate", None),
])
def test_v3_date_phrases(text, expected):
    r = resolve_dates(text)
    assert (r and (r.start_iso, r.end_iso)) == expected or (r is None and expected is None)


def test_impossible_day_without_a_year_is_reported():
    from backend.queries.date_utils import find_impossible_day

    assert find_impossible_day("Strikes in Michigan on February 30") == "February 30"
    assert find_impossible_day("events on 4/31/2024") == "4/31/2024"
    assert find_impossible_day("events on July 4") is None


def test_two_explicit_ranges_are_compared():
    earlier, later = resolve_comparison_periods(
        "Compare demonstrations in Ottawa from 2024-03-01 to 2024-03-15 versus 2024-02-01 to 2024-02-15")
    assert (earlier.start_iso, earlier.end_iso, later.start_iso, later.end_iso) == (
        "2024-02-01", "2024-02-15", "2024-03-01", "2024-03-15")
