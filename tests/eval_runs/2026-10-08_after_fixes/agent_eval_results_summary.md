# Agent evaluation, 2026-10-08 (after the fixes)

Run against the live `/api/v1/analyze` endpoint (`docker compose up db backend`), with the full 2024 North America
dataset restored from `gdelt_mysql_backup.sql` (17,480,236 events, 2024-01-01 to 2024-12-31), Ollama running
qwen2.5:3b locally, and the Kimi key from `.env` for the remote fallback. Raw results: `agent_eval_results.json`.

## Overall

**31 / 31 passed**, 0 failed, 0 without an assertion. Three consecutive runs gave the same result.

| Set | 2026-07-14 (before) | 2026-10-08 (after) |
| :--- | ---: | ---: |
| The original 24 questions | 13 / 24 (1 had no assertion) | 24 / 24 |
| 7 questions added with the fixes (comparison, month ends, "march" the noun, an impossible date) | not run | 7 / 7 |

The set was made stricter, never looser (revision 3 in `agent_eval_set.json`): `reldate-04` was notes-only and now
asserts the tool and the December range; `invalid-date-01` now also asserts the queried range (see below).

## What the live run found that the offline tests had not

The planner was first scored with the real Ollama router but without the database: 27 / 31. The failures, and the
ones that only appeared once the database was back, were each fixed with a regression test in
`tests/test_planner_offline.py`:

1. **The router's label decided whether a comparison was detected.** "Did protests increase in Canada in March
   compared with February?" was labelled a daily brief, and comparison detection only ran for search, overview and
   hot. It now runs on the user's words for every intent except event detail.
2. **"How has California been this month" was a daily brief.** The router labelled it brief (California) or search
   (Texas), and for California it also dropped the location into `query_text`. "How has X been / is X doing" with a
   known place is now a regional overview, and the place is recovered from the sentence.
3. **An impossible date was silently repaired.** For "what happened in Texas on 2024-02-30" the deterministic parser
   skipped the date and the router substituted 2024-02-29. Impossible dates are now reported.
4. **The notice could contradict the plan.** After fix 3, a run passed `invalid-date-01` (a notice was present) while
   the plan queried 2024-12-30 and the notice claimed the full year. Notices now state only what was not used, and
   the planner appends the window it actually queries. The eval item now asserts that window too.
5. **Four more wall-clock defaults** in `core_queries.py` (search, regional overview, hot events, daily brief
   fallbacks) used `datetime.now()`, giving 2026 windows over 2024 data. They use the dataset anchor now.
6. **Hot events returned nothing for every day.** The restored `daily_summary.hot_event_fingerprints` was the
   string `"[]"` for all 366 days; a non-empty string, so the precomputed branch ran, looped over nothing and returned
   `[]` instead of falling back to the live query it already had.

## Still true, and not hidden by the pass rate

* The set scores **tool selection and date ranges**, not answer quality.
* At the time of this run `search-01` and `keyword-fts-01` routed correctly but returned **0 rows**: keyword search
  read `event_fingerprints`, which was empty, and matched the whole sentence with one `LIKE`. **Fixed later the same
  day** (see `docs/DATA_LAYER.md`): after the fingerprint backfill and a FULLTEXT search on topic words, both return
  50 rows and the set is still 31/31. All other questions returned real rows (for example the Fort Worth event for
  `detail-01`, 858 vs 1,193 Canadian protest events for `compare-02`).

## Environment set-up (needed to reproduce)

* `gdelt_mysql_backup.sql` is **UTF-16LE with CRLF** line ends (a PowerShell redirect), so `mysql` rejects it as is.
  It was streamed through `iconv -f UTF-16 -t UTF-8` and `sed 's/\r$//'`.
* The `ActionGeo_Point` bytes did not survive that re-encode (`ERROR 1416`), so the column was imported as `LONGBLOB`
  and its spatial index skipped. The backend does not read this column. To restore it, rebuild from lat/long as
  `db_scripts/import_event.py` does (`ST_PointFromText('POINT(lat long)', 4326)`).
* The dump predates `event_fingerprints` and `region_daily_stats`; they were created with
  `db_scripts/precompute_tables.sql` (minus its `INSERT ... CURDATE()` placeholder row), empty at the time of this
  run, and backfilled afterwards with `db_scripts/backfill_precompute.py`; `ActionGeo_Point` was rebuilt from lat/long
  as well (`docs/DATA_LAYER.md`).
