"""Deterministic period comparison: the arithmetic the LLM must never do."""
import pytest

from backend.services.period_compare import LOW_VOLUME_EVENTS, PeriodCount, compare_counts


def period(label, days, events, start="2024-01-01", end="2024-01-31"):
    return PeriodCount(label=label, start=start, end=end, days=days, event_count=events)


def test_increase_is_decided_on_per_day_rate():
    r = compare_counts(period("Feb", 29, 2900), period("Mar", 31, 4650))
    assert r["direction"] == "increase"
    assert r["percent_change_per_day"] == 50.0
    assert r["absolute_change"] == 1750


def test_longer_month_with_more_total_events_can_still_be_a_decrease():
    # 31 days beat 29 days on totals, but the daily rate fell.
    r = compare_counts(period("Feb", 29, 2900), period("Mar", 31, 2930))
    assert r["absolute_change"] > 0
    assert r["direction"] == "decrease"
    assert any("different lengths" in c for c in r["caveats"])


def test_small_change_is_flat_not_a_direction():
    r = compare_counts(period("A", 30, 3000), period("B", 30, 3060))
    assert r["direction"] == "flat"


def test_low_volume_is_inconclusive():
    r = compare_counts(period("A", 30, LOW_VOLUME_EVENTS - 1), period("B", 30, 100))
    assert r["direction"] == "inconclusive_low_volume"


def test_zero_in_both_periods_is_no_data_not_no_change():
    r = compare_counts(period("A", 30, 0), period("B", 30, 0))
    assert r["direction"] == "no_data"
    assert r["percent_change_per_day"] is None


def test_zero_baseline_has_no_percentage():
    r = compare_counts(period("A", 30, 0), period("B", 30, 120))
    assert r["direction"] == "new_activity"
    assert r["percent_change_per_day"] is None


@pytest.mark.parametrize("a,b,word", [(1000, 1500, "up"), (1500, 1000, "down")])
def test_verdict_wording_matches_direction(a, b, word):
    r = compare_counts(period("A", 30, a), period("B", 30, b))
    assert f" is {word} " in r["verdict"]
