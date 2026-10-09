#!/usr/bin/env python3
"""
Backfill the precomputed tables for a date range, month by month, in SQL.

    python db_scripts/backfill_precompute.py --start 2024-01-01 --end 2024-12-31
    python db_scripts/backfill_precompute.py --verify 2000       # compare SQL rows with the Python reference

Steps (all idempotent, safe to re-run):
  schema        add the columns / FULLTEXT index the precompute SQL needs
  fingerprints  event_fingerprints for every event (headline, summary, source_text)
  fulltext      FULLTEXT(headline, source_text), built once after the bulk load
  regions       region_daily_stats for countries and first-level divisions
  hot           daily_summary.hot_event_fingerprints (top 20 per day)

Uses DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME, so inside the backend container:
    docker exec gdelt_backend python db_scripts/backfill_precompute.py
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
from datetime import date
from typing import Iterator, Tuple

import pymysql

sys.path.insert(0, os.path.dirname(__file__))
from precompute_sql import (  # noqa: E402
    ADM1_COLUMN,
    ADM1_INDEX,
    FULLTEXT_INDEX,
    HOT_FINGERPRINTS_SQL,
    SCHEMA_UPGRADES,
    WIDEN_REGION_CODE,
    fingerprint_row,
    insert_select_sql,
    region_stats_sql,
)

STEPS = ("schema", "fingerprints", "fulltext", "regions", "hot")


def connect():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3307")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", "rootpassword"),
        database=os.getenv("DB_NAME", "gdelt"),
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        read_timeout=3600,
        write_timeout=3600,
        # INSERT ... SELECT under REPEATABLE READ takes shared locks on every source row;
        # READ COMMITTED reads events_table without locking it.
        init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
    )


def months(start: date, end: date) -> Iterator[Tuple[str, str]]:
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = max(date(y, m, 1), start)
        last = min(date(y, m, calendar.monthrange(y, m)[1]), end)
        yield first.isoformat(), last.isoformat()
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def run(cur, label: str, sql: str, params=()) -> int:
    t0 = time.time()
    cur.execute(sql, params)
    print(f"  {label}: {cur.rowcount} rows affected in {time.time() - t0:.1f}s", flush=True)
    return cur.rowcount


def has_column(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


def has_index(cur, table: str, index: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
        (table, index),
    )
    return cur.fetchone() is not None


def step_schema(cur) -> None:
    print("schema", flush=True)
    for (table, column), ddl in SCHEMA_UPGRADES.items():
        if not has_column(cur, table, column):
            run(cur, f"add {table}.{column}", ddl)
    run(cur, "widen region_daily_stats.region_code", WIDEN_REGION_CODE)
    # State rows keyed by GDELT's damaged spellings ("YUCATÁMX") from before geo_names existed.
    from backend.queries.geo_names import DAMAGED_ADM1
    stale = [raw.upper() for raw in DAMAGED_ADM1]
    cur.execute(f"DELETE FROM region_daily_stats WHERE region_type = 'state' AND region_code IN ({', '.join(['%s'] * len(stale))})", stale)
    print(f"  removed {cur.rowcount} state rows under damaged names", flush=True)
    if not has_column(cur, "events_table", "ActionGeo_ADM1"):
        run(cur, "add events_table.ActionGeo_ADM1 (virtual)", ADM1_COLUMN)
    if not has_index(cur, "events_table", "idx_adm1_date_root_articles"):
        run(cur, "index ActionGeo_ADM1, SQLDATE, EventRootCode, NumArticles", ADM1_INDEX)
    # precompute_tables.sql declared fingerprint UNIQUE *and* indexed it again; the copy only slows inserts.
    if has_index(cur, "event_fingerprints", "fingerprint") and has_index(cur, "event_fingerprints", "idx_fingerprint"):
        run(cur, "drop duplicate idx_fingerprint", "ALTER TABLE event_fingerprints DROP INDEX idx_fingerprint")


def verify(cur, sample: int) -> bool:
    """Compare SQL-built fingerprint rows with ``fingerprint_row`` on real events."""
    cur.execute(
        "SELECT e.* FROM events_table e JOIN event_fingerprints f ON f.global_event_id = e.GlobalEventID "
        "WHERE e.GlobalEventID %% 997 = 0 LIMIT %s",
        (sample,),
    )
    events = cur.fetchall()
    mismatches = 0
    for evt in events:
        cur.execute("SELECT * FROM event_fingerprints WHERE global_event_id = %s", (evt["GlobalEventID"],))
        got = cur.fetchone()
        want = fingerprint_row(evt)
        for key, value in want.items():
            actual = got[key]
            if key == "key_actors":
                actual = json.loads(actual) if isinstance(actual, str) else actual
            if key == "severity_score":
                same = abs(float(actual) - value) < 1e-4
            else:
                same = actual == value
            if not same:
                mismatches += 1
                if mismatches <= 10:
                    print(f"  MISMATCH {evt['GlobalEventID']} {key}: sql={actual!r} python={value!r}")
    print(f"verify: {len(events)} events compared, {mismatches} field mismatches", flush=True)
    return mismatches == 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--steps", default=",".join(STEPS), help=f"comma-separated subset of {STEPS}")
    p.add_argument("--verify", type=int, default=0, help="only compare N fingerprint rows with the Python reference")
    args = p.parse_args()

    conn = connect()
    cur = conn.cursor()
    if args.verify:
        return 0 if verify(cur, args.verify) else 1

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    t_all = time.time()

    if "schema" in steps:
        step_schema(cur)
    if "fingerprints" in steps:
        sql = insert_select_sql("e.SQLDATE BETWEEN %s AND %s")
        for lo, hi in months(start, end):
            print(f"fingerprints {lo}..{hi}", flush=True)
            run(cur, "insert", sql, (lo, hi))
    if "fulltext" in steps and not has_index(cur, "event_fingerprints", "ft_headline_source"):
        print("fulltext", flush=True)
        run(cur, "build FULLTEXT(headline, source_text)", FULLTEXT_INDEX)
    if "regions" in steps:
        for lo, hi in months(start, end):
            print(f"regions {lo}..{hi}", flush=True)
            for region_type in ("country", "state"):
                run(cur, region_type, region_stats_sql(region_type), (lo, hi, lo, hi, lo, hi))
    if "hot" in steps:
        for lo, hi in months(start, end):
            print(f"hot {lo}..{hi}", flush=True)
            run(cur, "daily_summary", HOT_FINGERPRINTS_SQL, (lo, hi))

    print(f"done in {time.time() - t_all:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
