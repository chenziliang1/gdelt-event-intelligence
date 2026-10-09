"""
Read GDELT 2.0 event exports straight from data.gdeltproject.org.

Each 15-minute batch is ``http://data.gdeltproject.org/gdeltv2/<YYYYMMDDHHMMSS>.export.CSV.zip``,
a tab-separated file with 61 columns and no header. ``DATEADDED`` (column 59) is the batch
timestamp, so an event stored in events_table can be found again from its DATEADDED.

Used to repair text the July backup damaged and to build new periods (2025) with exactly the
columns of events_table. ``parse_row`` is checked column by column against existing rows by
``python db_scripts/gdelt_raw.py --verify``.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

# Two streams per batch: English sources, and translated non-English sources ("translingual").
# BigQuery's gdeltv2.events, which the 2024 extract came from, contains both.
STREAMS = {
    "english": "http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip",
    "translation": "http://data.gdeltproject.org/gdeltv2/{stamp}.translation.export.CSV.zip",
}
NA_COUNTRIES = ("US", "CA", "MX")  # FIPS codes; the extract in events_table is these three
CACHE_DIR = Path(os.getenv("GDELT_RAW_CACHE", Path(__file__).resolve().parents[1] / "data" / "gdelt_raw"))

# GDELT 2.0 event export columns used by events_table (0-based positions in the 61-column file).
COLUMNS = {
    "GlobalEventID": 0, "SQLDATE": 1, "MonthYear": 2,
    "Actor1Name": 6, "Actor1CountryCode": 7, "Actor1Type1Code": 12,
    "Actor2Name": 16, "Actor2CountryCode": 17, "Actor2Type1Code": 22,
    "EventCode": 26, "EventRootCode": 28, "QuadClass": 29, "GoldsteinScale": 30,
    "NumMentions": 31, "NumSources": 32, "NumArticles": 33, "AvgTone": 34,
    "ActionGeo_Type": 51, "ActionGeo_FullName": 52, "ActionGeo_CountryCode": 53,
    "ActionGeo_Lat": 56, "ActionGeo_Long": 57, "DATEADDED": 59, "SOURCEURL": 60,
}
N_COLUMNS = 61


def stamps(start: datetime, end: datetime) -> Iterator[str]:
    """15-minute batch stamps from ``start`` to ``end`` inclusive."""
    t = start.replace(minute=start.minute - start.minute % 15, second=0, microsecond=0)
    while t <= end:
        yield t.strftime("%Y%m%d%H%M%S")
        t += timedelta(minutes=15)


def fetch(stamp: str, stream: str = "english", retries: int = 3) -> Optional[bytes]:
    """The raw TSV bytes of one batch (cached on disk), or None if GDELT has no such file."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = STREAMS[stream].format(stamp=stamp)
    cached = CACHE_DIR / url.rsplit("/", 1)[1]
    if not cached.exists():
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    cached.write_bytes(resp.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:  # GDELT skips a few batches
                    return None
                time.sleep(2 * (attempt + 1))
            except Exception:  # noqa: BLE001
                time.sleep(2 * (attempt + 1))
        else:
            return None
    with zipfile.ZipFile(cached) as z:
        return z.read(z.namelist()[0])


def _num(value: str, cast):
    return cast(value) if value not in ("", None) else None


def parse_row(fields: List[str]) -> Dict[str, object]:
    """One 61-field export line -> an events_table row (same types as the DB returns)."""
    if len(fields) != N_COLUMNS:
        raise ValueError(f"expected {N_COLUMNS} fields, got {len(fields)}")
    f = {name: fields[i] for name, i in COLUMNS.items()}
    sqldate = f["SQLDATE"]
    added = f["DATEADDED"]
    return {
        "GlobalEventID": int(f["GlobalEventID"]),
        "SQLDATE": f"{sqldate[:4]}-{sqldate[4:6]}-{sqldate[6:8]}",
        "MonthYear": int(f["MonthYear"]),
        "DATEADDED": f"{added[:4]}-{added[4:6]}-{added[6:8]} {added[8:10]}:{added[10:12]}:{added[12:14]}",
        "Actor1Name": f["Actor1Name"], "Actor1CountryCode": f["Actor1CountryCode"], "Actor1Type1Code": f["Actor1Type1Code"],
        "Actor2Name": f["Actor2Name"], "Actor2CountryCode": f["Actor2CountryCode"], "Actor2Type1Code": f["Actor2Type1Code"],
        "EventCode": f["EventCode"], "EventRootCode": f["EventRootCode"],
        "QuadClass": _num(f["QuadClass"], int), "GoldsteinScale": _num(f["GoldsteinScale"], float),
        "AvgTone": _num(f["AvgTone"], float), "NumArticles": _num(f["NumArticles"], int),
        "NumMentions": _num(f["NumMentions"], int), "NumSources": _num(f["NumSources"], int),
        "ActionGeo_Type": _num(f["ActionGeo_Type"], int), "ActionGeo_FullName": f["ActionGeo_FullName"],
        "ActionGeo_CountryCode": f["ActionGeo_CountryCode"],
        "ActionGeo_Lat": _num(f["ActionGeo_Lat"], float), "ActionGeo_Long": _num(f["ActionGeo_Long"], float),
        "SOURCEURL": f["SOURCEURL"],
    }


def rows(stamp: str, countries: Iterable[str] = NA_COUNTRIES) -> List[Dict[str, object]]:
    """Both streams of one batch, filtered to ``countries`` (ActionGeo_CountryCode)."""
    keep = set(countries)
    out = []
    for stream in STREAMS:
        raw = fetch(stamp, stream)
        if raw is None:
            continue
        # QUOTE_NONE: GDELT fields are tab-separated and never quoted; a stray quote is data.
        for fields in csv.reader(io.StringIO(raw.decode("utf-8", errors="replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(fields) == N_COLUMNS and fields[COLUMNS["ActionGeo_CountryCode"]] in keep:
                out.append(parse_row(fields))
    return out


def rows_for(stamp_list: Iterable[str], workers: int = 16, countries: Iterable[str] = NA_COUNTRIES) -> Dict[str, List[Dict]]:
    stamp_list = list(stamp_list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(stamp_list, pool.map(lambda s: rows(s, countries), stamp_list)))


# ---------------------------------------------------------------------------
# Verification against events_table
# ---------------------------------------------------------------------------

def _db():
    import pymysql
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "3307")),
        user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", "rootpassword"),
        database=os.getenv("DB_NAME", "gdelt"), cursorclass=pymysql.cursors.DictCursor)


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-4
    return str(a) == str(b)


def verify(day: str) -> int:
    """Compare every column of every events_table row added on ``day`` with the official file."""
    start = datetime.strptime(day, "%Y-%m-%d")
    parsed = rows_for(stamps(start, start + timedelta(hours=23, minutes=45)))
    official = {r["GlobalEventID"]: r for batch in parsed.values() for r in batch}
    cols = list(next(iter(official.values())).keys())
    with _db() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(cols)} FROM events_table WHERE DATEADDED >= %s AND DATEADDED < %s",
            (day, (start + timedelta(days=1)).strftime("%Y-%m-%d")))
        stored = {r["GlobalEventID"]: r for r in cur.fetchall()}
    missing_in_db = set(official) - set(stored)
    missing_official = set(stored) - set(official)
    mismatched = {}
    for gid in set(official) & set(stored):
        for c in cols:
            if not _same(official[gid][c], stored[gid][c]):
                mismatched.setdefault(c, []).append(gid)
    print(f"{day}: official NA rows {len(official)}, stored rows {len(stored)}, "
          f"only official {len(missing_in_db)}, only stored {len(missing_official)}")
    for c, gids in mismatched.items():
        print(f"  column {c}: {len(gids)} mismatches, e.g. {gids[:3]}")
    if missing_in_db:
        sample = sorted(missing_in_db)[:5]
        print("  only official, SQLDATE of a sample:", [official[g]["SQLDATE"] for g in sample])
    return 0 if not mismatched else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify", metavar="YYYY-MM-DD", help="compare a day of events_table with the official files")
    args = p.parse_args()
    if args.verify:
        sys.exit(verify(args.verify))
