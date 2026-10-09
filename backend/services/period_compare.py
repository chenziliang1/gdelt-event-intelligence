"""
Deterministic period-over-period comparison.

The question "did X increase this month?" needs two *complete* counts and an
arithmetic comparison. The LLM must not do that arithmetic and the event
search tool (which returns the top-N events by article count) cannot supply
the counts. This module owns the arithmetic and the wording of the verdict;
the SQL that produces the counts lives in ``core_queries.query_period_counts``.

No I/O, no LLM: pure functions, easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# A change smaller than this (relative to the earlier period's daily rate)
# is reported as "about flat" rather than as a direction.
FLAT_THRESHOLD_PCT = 5.0
# With fewer events than this in either period, a percentage is not
# trustworthy; the verdict says so instead of declaring a direction.
LOW_VOLUME_EVENTS = 30


@dataclass(frozen=True)
class PeriodCount:
    label: str
    start: str
    end: str
    days: int
    event_count: int
    article_count: int = 0

    @property
    def per_day(self) -> float:
        return self.event_count / self.days if self.days else 0.0


def compare_counts(earlier: PeriodCount, later: PeriodCount) -> Dict[str, Any]:
    """Compare two periods using both totals and per-day rates.

    Periods can differ in length (February vs March), so the direction is
    decided on the per-day rate, never on raw totals.
    """
    caveats: List[str] = []
    rate_a, rate_b = earlier.per_day, later.per_day

    if earlier.days != later.days:
        caveats.append(
            f"Periods have different lengths ({earlier.days} vs {later.days} days); "
            "direction is based on events per day."
        )

    if earlier.event_count == 0 and later.event_count == 0:
        direction, pct = "no_data", None
        caveats.append("No matching events in either period. This is 'no data', not 'no change'.")
    elif earlier.event_count == 0:
        direction, pct = "new_activity", None
        caveats.append("The earlier period has no matching events, so a percentage change is undefined.")
    else:
        pct = (rate_b - rate_a) / rate_a * 100.0
        if min(earlier.event_count, later.event_count) < LOW_VOLUME_EVENTS:
            direction = "inconclusive_low_volume"
            caveats.append(
                f"Fewer than {LOW_VOLUME_EVENTS} matching events in at least one period; "
                "the change is within what random variation could produce."
            )
        elif abs(pct) < FLAT_THRESHOLD_PCT:
            direction = "flat"
        else:
            direction = "increase" if pct > 0 else "decrease"

    return {
        "earlier": _period_dict(earlier),
        "later": _period_dict(later),
        "absolute_change": later.event_count - earlier.event_count,
        "per_day_change": round(rate_b - rate_a, 2),
        "percent_change_per_day": None if pct is None else round(pct, 1),
        "direction": direction,
        "verdict": _verdict(direction, earlier, later, pct),
        "caveats": caveats,
    }


def _period_dict(p: PeriodCount) -> Dict[str, Any]:
    return {
        "label": p.label,
        "start": p.start,
        "end": p.end,
        "days": p.days,
        "event_count": p.event_count,
        "events_per_day": round(p.per_day, 2),
        "article_count": p.article_count,
    }


def _verdict(direction: str, a: PeriodCount, b: PeriodCount, pct: Optional[float]) -> str:
    if direction == "no_data":
        return f"No matching events were found in {a.label} or {b.label}."
    if direction == "new_activity":
        return f"{b.label} had {b.event_count:,} matching events; {a.label} had none."
    if direction == "inconclusive_low_volume":
        return (
            f"Too few events to call a direction: {a.event_count:,} in {a.label} "
            f"and {b.event_count:,} in {b.label}."
        )
    if direction == "flat":
        return (
            f"About flat: {a.event_count:,} events in {a.label} vs {b.event_count:,} "
            f"in {b.label} ({pct:+.1f}% per day)."
        )
    word = "up" if direction == "increase" else "down"
    return (
        f"{b.label} is {word} {abs(pct):.1f}% per day versus {a.label} "
        f"({a.event_count:,} to {b.event_count:,} events)."
    )
