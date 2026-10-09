#!/usr/bin/env python3
"""
Check where the damaged ActionGeo_FullName values come from, using the official GDELT files.

132,609 Mexican rows (781 distinct place names) carry state names with bytes missing after an
accented character ("México" stored as "Méco"). The first guess was that the July backup's
PowerShell UTF-16 redirect did it. This script looks up sample events of every damaged name in the
official 15-minute batch named by their DATEADDED and compares: on 2026-10-09 all 781 names were
spelled exactly the same upstream (2,014 sample events, 0 not found, 0 ambiguous). So the damage
is in GDELT; events_table keeps the source spelling and backend/queries/geo_names.py maps the six
state names for search and statistics. Nothing in the database is changed.

    python db_scripts/check_damaged_names.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gdelt_raw  # noqa: E402

DAMAGED = "ActionGeo_FullName REGEXP '[^ -~]'"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--mapping-out", default=str(Path(__file__).resolve().parents[1] / "docs" / "data_repair" / "mx_names_upstream_check.json"))
    args = p.parse_args()

    conn = gdelt_raw._db()
    cur = conn.cursor()
    cur.execute(f"""
        SELECT ActionGeo_FullName AS name, GlobalEventID AS gid, DATE_FORMAT(DATEADDED, '%%Y%%m%%d%%H%%i%%s') AS stamp
        FROM (
            SELECT ActionGeo_FullName, GlobalEventID, DATEADDED,
                   ROW_NUMBER() OVER (PARTITION BY ActionGeo_FullName ORDER BY GlobalEventID) AS rn
            FROM events_table WHERE {DAMAGED}
        ) t WHERE rn <= %s
    """, (args.samples,))
    samples = cur.fetchall()
    stamps = sorted({s["stamp"] for s in samples})
    print(f"{len({s['name'] for s in samples})} damaged names, {len(samples)} sample events, {len(stamps)} batches")
    parsed = gdelt_raw.rows_for(stamps, countries=("MX",))
    official = {r["GlobalEventID"]: r["ActionGeo_FullName"] for batch in parsed.values() for r in batch}

    found = defaultdict(set)
    unresolved = []
    for s in samples:
        name = official.get(s["gid"])
        if name is None:
            unresolved.append(s["gid"])
        else:
            found[s["name"]].add(name)
    mapping = {bad: next(iter(good)) for bad, good in found.items() if len(good) == 1}
    ambiguous = {bad: sorted(good) for bad, good in found.items() if len(good) > 1}
    print(f"resolved {len(mapping)}, ambiguous {len(ambiguous)}, sample events not found {len(unresolved)}")
    for bad, good in list(mapping.items())[:8]:
        print(f"  {bad!r} -> {good!r}")
    if ambiguous:
        print("  ambiguous:", list(ambiguous.items())[:5])

    Path(args.mapping_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.mapping_out).write_text(json.dumps({"mapping": mapping, "ambiguous": ambiguous}, ensure_ascii=False, indent=1))

    same = sum(1 for bad, good in mapping.items() if bad == good)
    print(f"spelled the same in the official files: {same} of {len(mapping)} names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
