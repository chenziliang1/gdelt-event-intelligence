"""
The query layer against a real MySQL with a real slice of the data (CI job "db-integration").

tests/ci_db_setup.sh loads tests/fixtures/gdelt_slice.sql.gz (all Texas, Ontario and Estado de
México events of 2024-02-26..03-03, and the Fort Worth event of 2024-01-09) and derives the
precomputed tables with the repository's backfill. Expected values were computed on that slice;
the Texas counts equal the full 2024 database's (the slice holds every Texas event of the week).

Skipped unless GDELT_TEST_DB=1 (the offline suite has no database).
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(os.getenv("GDELT_TEST_DB") != "1", reason="needs the CI MySQL (tests/ci_db_setup.sh)")

WEEK = ("2024-02-26", "2024-03-03")


@pytest.fixture(scope="module")
def r():
    """Run every query once, in one event loop (the pool is bound to its loop)."""
    from backend.database.pool import DatabasePool
    from backend.queries import core_queries as Q

    async def main():
        pool = await DatabasePool.initialize()
        try:
            out = {}
            out["search_texas_protest"] = await Q.query_search_events(
                pool, start_date=WEEK[0], end_date=WEEK[1], location_hint="Texas", event_type="protest", max_results=50)
            out["counts_texas_protest"] = await Q.query_period_counts(pool, [
                {"label": "Feb 26-29", "start": "2024-02-26", "end": "2024-02-29"},
                {"label": "Mar 1-3", "start": "2024-03-01", "end": "2024-03-03"}], location_hint="Texas", event_type="protest")
            out["counts_estado_de_mexico"] = await Q.query_period_counts(
                pool, [{"label": "week", "start": WEEK[0], "end": WEEK[1]}], location_hint="Estado de México")
            out["counts_estado_de_mexico_without_accent"] = await Q.query_period_counts(
                pool, [{"label": "week", "start": WEEK[0], "end": WEEK[1]}], location_hint="Estado de Mexico")
            out["counts_canada"] = await Q.query_period_counts(
                pool, [{"label": "week", "start": WEEK[0], "end": WEEK[1]}], location_hint="Canada")
            out["overview_texas"] = await Q.query_regional_overview(pool, "Texas", start_date=WEEK[0], end_date=WEEK[1])
            out["detail_by_id"] = await Q.query_event_detail(pool, "1150442224")
            out["detail_by_fingerprint"] = await Q.query_event_detail(pool, "US-20240109-FOR-APPEAL-1150442224")
            out["detail_missing"] = await Q.query_event_detail(pool, "9999999999")
            out["hot_2024_02_27"] = await Q.query_hot_events(pool, query_date="2024-02-27")
            out["keyword_wildfire"] = await Q.query_search_events(
                pool, query_text="search for headline mentioning wildfire", start_date=WEEK[0], end_date=WEEK[1], max_results=50)
            ids = [row["GlobalEventID"] for row in out["keyword_wildfire"]]
            out["wildfire_source_text"] = await pool.fetchall(
                f"SELECT source_text FROM event_fingerprints WHERE global_event_id IN ({', '.join(['%s'] * len(ids))})", tuple(ids))
            return out
        finally:
            await DatabasePool.close()

    return asyncio.run(main())


def test_state_search_finds_city_level_events(r):
    rows = r["search_texas_protest"]
    assert len(rows) == 50 and rows[0]["NumArticles"] == 30
    assert all(row["ActionGeo_FullName"].endswith("Texas, United States") or row["ActionGeo_FullName"].startswith("Texas")
               for row in rows)
    city_level = [row for row in rows if not row["ActionGeo_FullName"].startswith("Texas")]
    assert len(city_level) == 21  # a prefix-only match ("Texas%") returned none of these


def test_period_counts_are_complete_counts(r):
    assert [(p["event_count"], p["article_count"]) for p in r["counts_texas_protest"]] == [(178, 761), (78, 401)]


def test_comparison_verdict_from_the_counts(r):
    from backend.services.period_compare import PeriodCount, compare_counts

    a, b = (PeriodCount(p["label"], p["start"], p["end"], d, p["event_count"], p["article_count"])
            for p, d in zip(r["counts_texas_protest"], (4, 3)))
    result = compare_counts(a, b)
    assert result["direction"] == "decrease" and result["percent_change_per_day"] == -41.6


def test_damaged_mexican_state_name_is_found_and_multi_word_names_stay_whole(r):
    # GDELT stores the state as "Méco"; the query also used to split the name into "Estado%", "de%"
    # (Germany's country code DE) and "México" (all of Mexico) and returned 2,073.
    assert r["counts_estado_de_mexico"][0]["event_count"] == 1798
    assert r["counts_estado_de_mexico_without_accent"][0]["event_count"] == 1798


def test_country_names_use_the_country_code(r):
    assert r["counts_canada"][0]["event_count"] == 8250  # the slice's Canadian events are Ontario's


def test_regional_overview_uses_the_precomputed_tables(r):
    ov = r["overview_texas"]
    assert ov["source"] == "precomputed" and ov["summary"]["total"] == 24947
    assert [h["NumArticles"] for h in ov["hot_events"]] == [140, 102, 80, 80, 80]


def test_event_detail_by_id_and_by_fingerprint(r):
    for key in ("detail_by_id", "detail_by_fingerprint"):
        assert r[key]["location_name"] == "Fort Worth, Texas, United States"
    assert r["detail_missing"] is None


def test_hot_events_come_from_the_stored_fingerprints(r):
    hot = r["hot_2024_02_27"]
    assert len(hot) == 5 and hot[0]["fp_type"] == "standard"
    assert hot[0]["fingerprint"] == "US-20240227-TEX-FORCE-1160247273"


def test_keyword_search_matches_topic_words_in_the_source_text(r):
    rows = r["keyword_wildfire"]
    assert len(rows) == 50
    assert all("wildfire" in (s["source_text"] or "") or "wildfire" in (s.get("headline") or "").lower()
               for s in r["wildfire_source_text"])
