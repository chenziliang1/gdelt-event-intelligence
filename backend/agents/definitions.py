"""
The definitions line shown with every answer: what was counted, where, and as of when.

An analyst cannot see the units otherwise: GDELT has one row per event and a NumArticles column,
so "how many protests" can mean events or articles, and a spreadsheet built one way will not
match an answer built the other. The tools count events; this line says so, with the place
field, the event-type definition, the date anchor and whether a list is a sample.
"""

from typing import Any, Dict, List

from backend.queries.query_utils import (
    DEFAULT_DATA_END,
    DEFAULT_DATA_REFERENCE,
    DEFAULT_DATA_START,
    EVENT_TYPE_CONDITIONS,
)

_SAMPLE_STEPS = {"events", "top_events", "hot_events", "similar_events"}
_TYPE_WORDS = {
    "protest": "protest = CAMEO root code 14",
    "conflict": "conflict = Goldstein score below -5",
    "cooperation": "cooperation = Goldstein score above 5",
}


def definitions_line(steps: List[Dict[str, Any]]) -> str:
    """One line built from the plan's steps; no LLM involved."""
    parts = ["Counts are GDELT event records, not articles",
             "location is where the event took place (ActionGeo)"]
    types = sorted({str(s.get("params", {}).get("event_type")) for s in steps
                    if s.get("params", {}).get("event_type") in EVENT_TYPE_CONDITIONS})
    parts += [_TYPE_WORDS[t] for t in types if t in _TYPE_WORDS]
    if any(s.get("type") in _SAMPLE_STEPS for s in steps):
        parts.append("event lists are the most-covered records, a sample, not totals")
    parts.append(f"data covers {DEFAULT_DATA_START} to {DEFAULT_DATA_END}; "
                 f"relative dates are resolved as of {DEFAULT_DATA_REFERENCE}")
    return "Definitions: " + "; ".join(parts) + "."
