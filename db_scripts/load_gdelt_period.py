#!/usr/bin/env python3
"""
Load a period of GDELT events from the official files into a separate table, with the same
rule that produced events_table (verified on 2024-03-05, 61,009 of 61,009 rows identical):
both streams (English and translated), ActionGeo_CountryCode in US / CA / MX, SQLDATE in range.

    python db_scripts/load_gdelt_period.py --start 2025-01-01 --end 2025-03-31 --added-until 2025-04-07 \
        --table events_2025

Events are often added days after they happen, so batches are read until --added-until.
The target table is separate from events_table so the application's 2024 data is unchanged.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gdelt_raw  # noqa: E402

COLS = list(gdelt_raw.COLUMNS)  # events_table columns except ActionGeo_Point


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--added-until", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--workers", type=int, default=16)
    args = p.parse_args()
    if args.table == "events_table":
        raise SystemExit("refusing to write into events_table")

    conn = gdelt_raw._db()
    cur = conn.cursor()
    cur.execute(f"CREATE TABLE IF NOT EXISTS {args.table} AS SELECT {', '.join(COLS)} FROM events_table WHERE 1 = 0")
    cur.execute(f"ALTER TABLE {args.table} ADD PRIMARY KEY (GlobalEventID), ADD INDEX idx_sqldate (SQLDATE)") if not _has_pk(cur, args.table) else None
    conn.commit()

    start = datetime.strptime(args.start, "%Y-%m-%d")
    until = datetime.strptime(args.added_until, "%Y-%m-%d") + timedelta(hours=23, minutes=45)
    stamps = list(gdelt_raw.stamps(start, until))
    lo, hi = args.start, args.end
    t0, kept, batches = time.time(), 0, 0
    placeholders = ", ".join(["%s"] * len(COLS))
    sql = f"INSERT IGNORE INTO {args.table} ({', '.join(COLS)}) VALUES ({placeholders})"
    for i in range(0, len(stamps), 96):  # one day of batches at a time
        chunk = stamps[i:i + 96]
        parsed = gdelt_raw.rows_for(chunk, workers=args.workers)
        rows = [r for batch in parsed.values() for r in batch if lo <= r["SQLDATE"] <= hi]
        if rows:
            cur.executemany(sql, [tuple(r[c] for c in COLS) for r in rows])
            conn.commit()
        kept += len(rows)
        batches += sum(1 for v in parsed.values() if v)
        print(f"{chunk[0][:8]}: {len(rows)} rows kept (total {kept}, {time.time() - t0:.0f}s)", flush=True)
    cur.execute(f"SELECT COUNT(*) AS n, MIN(SQLDATE) AS lo, MAX(SQLDATE) AS hi FROM {args.table}")
    print("loaded:", cur.fetchone(), f"from {len(stamps)} batch stamps")
    return 0


def _has_pk(cur, table: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.table_constraints WHERE table_schema = DATABASE() "
                "AND table_name = %s AND constraint_type = 'PRIMARY KEY'", (table,))
    return cur.fetchone() is not None


if __name__ == "__main__":
    sys.exit(main())
