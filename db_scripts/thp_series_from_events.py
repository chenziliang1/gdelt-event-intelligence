#!/usr/bin/env python3
"""
Daily forecaster series computed directly from an events table.

The thp_* summary tables the forecaster was trained on came from a precompute script that is
no longer in the repository. This module rebuilds the same rows from events, so the model can
be evaluated on periods those tables never covered (2025). ``--verify`` recomputes 2024 days
and compares every series and column with the stored tables; it must report 0 differences
before the rebuilt rows are trusted.

    python db_scripts/thp_series_from_events.py --verify 2024-03-05 2024-07-19
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "db_scripts"))
from backend.queries.query_utils import FORECAST_EVENT_TYPE_CONDITIONS as FC  # noqa: E402
import gdelt_raw  # noqa: E402

METRICS = ("total_events", "conflict_events", "cooperation_events", "protest_events",
           "avg_goldstein", "avg_tone", "total_articles")


def _metrics_sql() -> str:
    return f"""COUNT(*) AS total_events,
        SUM({FC['conflict']}) AS conflict_events, SUM({FC['cooperation']}) AS cooperation_events,
        SUM({FC['protest']}) AS protest_events, AVG(GoldsteinScale) AS avg_goldstein,
        AVG(AvgTone) AS avg_tone, SUM(NumArticles) AS total_articles"""


def sql_series(cur, table: str, start: str, end: str) -> List[Dict]:
    """global, country, country_pair, event_root, event_code rows for [start, end]."""
    out: List[Dict] = []
    # global:ALL comes from daily_summary in training. Its stored conflict / cooperation counts are
    # Goldstein < 0 / > 0 (verified), not the |Goldstein| > 5 the repository's daily ETL computed.
    cur.execute(f"""
        SELECT 'global:ALL' AS series_id, SQLDATE AS event_date, COUNT(*) AS total_events,
               SUM({FC['conflict']}) AS conflict_events, SUM({FC['cooperation']}) AS cooperation_events,
               0 AS protest_events, AVG(GoldsteinScale) AS avg_goldstein, AVG(AvgTone) AS avg_tone,
               COUNT(*) AS total_articles
        FROM {table} WHERE SQLDATE BETWEEN %s AND %s GROUP BY SQLDATE""", (start, end))
    out += cur.fetchall()
    for prefix, key, where in (
        ("country", "ActionGeo_CountryCode", "ActionGeo_CountryCode <> ''"),
        ("country_pair",
         "CONCAT(LEAST(Actor1CountryCode, Actor2CountryCode), '-', GREATEST(Actor1CountryCode, Actor2CountryCode))",
         "Actor1CountryCode <> '' AND Actor2CountryCode <> '' AND Actor1CountryCode <> Actor2CountryCode"),
        ("event_root", "EventRootCode", "EventRootCode <> ''"),
        ("event_code", "EventCode", "EventCode <> ''"),
    ):
        cur.execute(f"""
            SELECT CONCAT('{prefix}:', {key}) AS series_id, SQLDATE AS event_date, {_metrics_sql()}
            FROM {table} WHERE SQLDATE BETWEEN %s AND %s AND {where}
            GROUP BY series_id, SQLDATE""", (start, end))
        out += cur.fetchall()
    return out


def _pair_name(value) -> str:
    """Actors and actor pairs were keyed on upper-cased names without the alias table (verified
    against the thp_actor tables: with aliases, "US" and "UNITED STATES" merged into extra rows)."""
    return " ".join((value or "").split()).upper()


def actor_series(cur, table: str, start: str, end: str) -> List[Dict]:
    """actor and actor_pair rows: names normalised in Python as in training."""
    cur.execute(f"""
        SELECT SQLDATE, Actor1Name, Actor2Name, GoldsteinScale, AvgTone, NumArticles, EventRootCode
        FROM {table} WHERE SQLDATE BETWEEN %s AND %s""", (start, end))
    acc: Dict[tuple, Dict] = defaultdict(lambda: {"n": 0, "conf": 0, "coop": 0, "prot": 0, "g": 0.0, "t": 0.0, "art": 0})

    def add(series_id, day, r):
        a = acc[(series_id, day)]
        g = r["GoldsteinScale"] or 0.0
        a["n"] += 1
        a["conf"] += g < 0
        a["coop"] += g > 0
        a["prot"] += r["EventRootCode"] == "14"
        a["g"] += g
        a["t"] += r["AvgTone"] or 0.0
        a["art"] += r["NumArticles"] or 0

    for r in cur.fetchall_unbuffered() if hasattr(cur, "fetchall_unbuffered") else cur.fetchall():
        # Upper-cased raw names, no alias table: verified against thp_actor_daily_summary
        # ("UNITED STATES OF AMERICA" and "U.S." are not counted as "UNITED STATES").
        a1, a2 = _pair_name(r["Actor1Name"]), _pair_name(r["Actor2Name"])
        day = r["SQLDATE"]
        # Training counted actor *appearances*: an event with the same actor on both sides counts twice.
        for name in (a1, a2):
            if name:
                add(f"actor:{name}", day, r)
        p1, p2 = _pair_name(r["Actor1Name"]), _pair_name(r["Actor2Name"])
        if p1 and p2 and p1 != p2:
            add(f"actor_pair:{' :: '.join(sorted((p1, p2)))}", day, r)
    return [
        {"series_id": sid, "event_date": day, "total_events": a["n"], "conflict_events": a["conf"],
         "cooperation_events": a["coop"], "protest_events": a["prot"], "avg_goldstein": a["g"] / a["n"],
         "avg_tone": a["t"] / a["n"], "total_articles": a["art"]}
        for (sid, day), a in acc.items()
    ]


STORED = {
    "country": ("thp_country_daily_summary", "country"),
    "country_pair": ("thp_country_pair_daily_summary", "country_pair"),
    "actor": ("thp_actor_daily_summary", "actor_name"),
    "actor_pair": ("thp_actor_pair_daily_summary", "actor_pair"),
    "event_root": ("thp_event_root_daily_summary", "event_root"),
    "event_code": ("thp_event_code_daily_summary", "event_code"),
}


def verify(days: Sequence[str]) -> int:
    bad = 0
    with gdelt_raw._db() as conn, conn.cursor() as cur:
        for day in days:
            rebuilt = {(r["series_id"], str(r["event_date"])): r
                       for r in sql_series(cur, "events_table", day, day) + actor_series(cur, "events_table", day, day)}
            cur.execute("SELECT 'global:ALL' AS series_id, date AS event_date, total_events, conflict_events, "
                        "cooperation_events, avg_goldstein, avg_tone FROM daily_summary WHERE date = %s", (day,))
            stored_rows = cur.fetchall()
            for prefix, (table, key) in STORED.items():
                cur.execute(f"SELECT CONCAT('{prefix}:', {key}) AS series_id, event_date, {', '.join(METRICS)} "
                            f"FROM {table} WHERE event_date = %s", (day,))
                stored_rows += cur.fetchall()
            per_prefix = defaultdict(lambda: [0, 0])
            for s in stored_rows:
                prefix = s["series_id"].split(":", 1)[0]
                r = rebuilt.get((s["series_id"], str(s["event_date"])))
                per_prefix[prefix][0] += 1
                ok = r is not None and all(
                    abs(float(s[m] or 0) - float(r[m] or 0)) <= (1e-3 if m.startswith("avg") else 0)
                    for m in METRICS if m in s)
                if not ok:
                    per_prefix[prefix][1] += 1
                    if per_prefix[prefix][1] <= 2:
                        print(f"  {day} {s['series_id']}: stored {[s.get(m) for m in METRICS]} rebuilt {None if r is None else [r.get(m) for m in METRICS]}")
            for prefix, (n, b) in sorted(per_prefix.items()):
                print(f"{day} {prefix:13s} stored rows {n:5d}  differing {b}")
                bad += b
    return 1 if bad else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify", nargs="+", metavar="YYYY-MM-DD")
    a = p.parse_args()
    if a.verify:
        sys.exit(verify(a.verify))
