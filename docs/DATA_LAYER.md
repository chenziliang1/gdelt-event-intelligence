# Data layer

What is in MySQL, where each table comes from, and how to rebuild it. Numbers are for the 2024 North America
extract restored on 2026-10-08, with the additions of 2026-10-09.

## Source data

`events_table`: 17,480,236 GDELT 2.0 events, event date 2024-01-01 to 2024-12-31, `ActionGeo_CountryCode IN ('US',
'CA', 'MX')` (US 15.2M, CA 1.27M, MX 1.02M). The import log names 100 BigQuery export shards
(`gdelt_2024_na_0000000000NN.csv`) loaded by `db_scripts/import_event.py`.

**The extract can be rebuilt from the public files.** `db_scripts/gdelt_raw.py --verify 2024-03-05` downloads that
day's 15-minute batches from `data.gdeltproject.org/gdeltv2/` and compares them with `events_table`: with both streams
(English and translated) and the filter above on event date, the official files give 61,009 rows and so does
`events_table`, identical in every column. The English stream alone misses 6,160 of them; the 767 extra official rows
of that day have event dates outside 2024.

`events_2025`: 4,638,688 events for 2025-01-01 to 2025-03-31, loaded with the same rule by
`db_scripts/load_gdelt_period.py` (batches read until 2025-04-07 for late additions). It is a separate table, used
only for the forecaster's fresh test (`docs/FORECAST_EVALUATION.md`); the application still serves 2024.

### Damaged Mexican state names (upstream)

132,609 Mexican rows (0.76%) carry six state names with bytes missing after the accented character: "Méco" for
México, "Nuevo LeóX", "Queréro de Arteaga", "YucatáMX", "Michoacáde Ocampo", "San Luis PotosíX". The official files
contain the same spellings (checked on 2,014 sample events across all 781 affected place names), so the damage is in
GDELT, not in the backup, and `events_table` keeps the source spelling. `backend/queries/geo_names.py` maps them to the
real names for region statistics and search ("Yucatan" and "Yucatán" both find "YucatáMX").

## Derived tables

All derived rows are defined once in `db_scripts/precompute_sql.py` and written by both the daily ETL
(`db_scripts/etl_pipeline.py <date>`) and the backfill (`db_scripts/backfill_precompute.py`). Every step is an
`INSERT ... SELECT` and idempotent.

| Table | Rows | What it holds | Used by |
| :--- | ---: | :--- | :--- |
| `event_fingerprints` | one per event | Readable ID (`US-20240109-FOR-APPEAL-1150442224`), template headline, summary, `source_text` (words of the source URL path), FULLTEXT index on headline + source_text | Event detail by fingerprint, keyword search |
| `region_daily_stats` | one per region and day | Countries (`US`) and first-level divisions (`TEXAS`, `ONTARIO`, `YUCATÁN`): counts, conflict / cooperation counts (Goldstein < -5 / > 5), averages, top 3 actors, the day's 5 most-covered event IDs | Regional overview |
| `daily_summary.hot_event_fingerprints` | 366 days | Top 20 events per day by NumArticles x abs(Goldstein), as fingerprints | Hot events (fast path) |
| `daily_summary` (other columns), `geo_heatmap_grid`, `thp_*` | from the backup | Daily totals, map grid, forecast training aggregates | Dashboard, map, forecaster |

`events_table.ActionGeo_ADM1` is a virtual column (the first-level division in `ActionGeo_FullName`) with the index
`idx_adm1_date_root_articles (ActionGeo_ADM1, SQLDATE, EventRootCode, NumArticles)`, added by the backfill's schema
step for location search.

Headlines are template text from the CAMEO root code, not model output (`llm_version = 'template-v2'`). GDELT has no
article titles; `source_text` is the closest thing and is what keyword search matches.

The `thp_*` forecaster tables came from a script that is not in the repository. `db_scripts/thp_series_from_events.py`
reconstructs them and matches the stored tables exactly on five 2024 days (every series, every column); see
`docs/FORECAST_EVALUATION.md` for what that showed about how they were counted.

### Fixed on the way

* **Location search missed most city-level events.** Event search matched `ActionGeo_FullName LIKE 'Texas%'`, which
  finds rows geocoded to the state ("Texas, United States") and misses "Austin, Texas, United States": 287 of 612
  Texas protests in March 2024, 32% of California's events. States now match the indexed `ActionGeo_ADM1` column and
  countries the indexed country code. On March 2024 this finds the same rows as a full `LIKE` scan, minus that scan's
  false matches ("Ontario County, New York", "Texas County, Missouri"), and a state-year search takes 0.3 s instead
  of 12.7 s.
* Fingerprints used the last 3 digits of GlobalEventID and could collide on the UNIQUE column, silently dropping
  the second event; they now end in the full ID.
* Headlines were mangled machine translations ("sendtablesoundclear"); the CAMEO root map had 05 to 09 wrong.
* Inserts were row by row (days for a year). The 2024 backfill now runs in SQL: fingerprints about 10 min, FULLTEXT
  build 18 min, region stats about 25 min (with the daily top-5 IDs), hot lists 2 min, on a laptop. One day, as the
  daily ETL runs it, takes seconds.
* Region stats used `MAX(Actor1Name)` and `MAX(ActionGeo_FullName)` (alphabetically last) as the primary actor and
  region name, covered countries only, and the overview read the last 7 daily rows as if they were the whole range,
  under a key the report never read. The overview now sums the whole range and takes its hot events from the stored
  daily top-5 IDs instead of scanning the range.
* Keyword search matched the whole sentence with one `LIKE '%...%'`; it now requires every topic word in the
  FULLTEXT index.
* The daily ETL counted `daily_summary` conflict / cooperation as |Goldstein| > 5, while the stored 2024 rows (and the
  forecaster's global series) use Goldstein < 0 / > 0. New days would not have matched the history; aligned.

### Checks (2026-10-09)

* `event_fingerprints`: 17,480,236 rows, one per event; `--verify` found 0 field mismatches between SQL and Python.
* `region_daily_stats`: 1,098 country-days and 35,994 division-days (101 divisions). Country totals add up to
  `events_table` exactly (all 17,480,236), division totals to every state- or city-level event (15,663,413), and the
  35,994 (division, day) pairs are exactly those present in `events_table`. (An earlier rebuild had 32,953; it was
  incomplete.)
* Hot lists: 20 fingerprints for each of the 366 days.
* `ActionGeo_Point`: rebuilt for all rows, SRID 4326, spatial index restored.
* Agent eval: original set 31/31, held-out v1 29/29 and v2 28/28 after fixes (first unseen runs 24/29 and 23/28);
  keyword questions return 50 rows each, hot events 10; a regional overview of Texas for 2024 takes 2.4 s end to end
  (26 s before the precomputed path, 10 s before the stored daily top events).

## Rebuilding

```bash
docker compose up -d db backend
docker exec -i gdelt_mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" gdelt' < db_scripts/precompute_tables.sql
docker exec gdelt_backend python db_scripts/backfill_precompute.py            # all steps, all of 2024
docker exec gdelt_backend python db_scripts/backfill_precompute.py --verify 2000   # SQL rows vs the Python reference
python db_scripts/build_known_locations.py                                    # regenerate backend/agents/known_locations.py
```

## Backups

`gdelt_mysql_backup.sql` (2026-07-07) is **UTF-16LE with CRLF line ends** (a PowerShell `>` redirect), so `mysql`
rejects it, and the binary `ActionGeo_Point` values did not survive the re-encode. It also predates
`event_fingerprints` and `region_daily_stats`. To restore it anyway: stream it through
`iconv -f UTF-16 -t UTF-8 | sed 's/\r$//'`, import `ActionGeo_Point` as `LONGBLOB`, then rebuild the column from
lat/long as `import_event.py` does (`ST_PointFromText('POINT(lat long)', 4326)`).

`gdelt_mysql_backup_2026-10-09.sql.gz` is the current state (all tables above, including `events_2025`). Restore
drill (2026-10-09): restored into a fresh `mysql:8.0` container (768 MB buffer pool, 1.5 GB memory limit) in 70
minutes with no errors. Row counts of every table, the region totals, the 366 hot lists, a sampled event (including
`ActionGeo_Point` with SRID 4326 and the virtual `ActionGeo_ADM1`), its fingerprint, the three special indexes and an
indexed query all matched the source. `CHECKSUM TABLE` matched for all tables compared except `region_daily_stats`,
whose FLOAT columns differ in their lowest bits: mysqldump writes FLOAT with 6 to 7 significant digits, and all 37,092
rows agree to every digit MySQL displays.

Make new backups with binary-safe, UTF-8 output and never through a PowerShell redirect:

```bash
docker exec gdelt_mysql sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --hex-blob \
  --default-character-set=utf8mb4 gdelt' | gzip > gdelt_mysql_backup_YYYY-MM-DD.sql.gz
```
