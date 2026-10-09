"""Precomputed tables: the Python reference row and the shape of the generated SQL.

The SQL itself runs against MySQL in ``backfill_precompute.py --verify``, which compares it
with ``fingerprint_row`` on real events.
"""
from precompute_sql import (
    CAMEO_ROOTS,
    HOT_FINGERPRINTS_SQL,
    fingerprint_row,
    insert_select_sql,
    location_code,
    region_stats_sql,
    source_text,
)

from backend.agents.planner import EVENT_ID_IN_TEXT

# A real row (the Fort Worth explosion coverage used by the agent eval).
FORT_WORTH = {
    "GlobalEventID": 1150442224,
    "SQLDATE": "2024-01-09",
    "Actor1Name": "AUTHORITIES",
    "Actor2Name": "",
    "EventRootCode": "02",
    "GoldsteinScale": 3.0,
    "NumArticles": 120,
    "ActionGeo_FullName": "Fort Worth, Texas, United States",
    "ActionGeo_CountryCode": "US",
    "SOURCEURL": "https://www.washingtontimes.com/news/2024/jan/8/fort-worth-explosion-debris-scattered-as-authoriti/",
}


def test_fingerprint_is_unique_per_event_and_readable():
    row = fingerprint_row(FORT_WORTH)
    # The full GlobalEventID, not its last 3 digits (which collided on the UNIQUE column).
    assert row["fingerprint"] == "US-20240109-FOR-APPEAL-1150442224"
    other = dict(FORT_WORTH, GlobalEventID=9990442224)  # same last 3 digits
    assert fingerprint_row(other)["fingerprint"] != row["fingerprint"]


def test_fingerprints_are_recognised_by_the_planner_for_every_country():
    for country in ("US", "CA", "MX"):
        fp = fingerprint_row(dict(FORT_WORTH, ActionGeo_CountryCode=country))["fingerprint"]
        assert EVENT_ID_IN_TEXT.search(f"tell me about {fp}").group(0) == fp


def test_location_code_is_letters_only():
    assert location_code("St. Louis, Missouri, United States") == "STL"
    assert location_code("") == "UNK"
    assert location_code("Montréal, Quebec, Canada") == "MON"


def test_headline_is_plain_english():
    row = fingerprint_row(FORT_WORTH)
    assert row["headline"] == "AUTHORITIES appeals (Fort Worth, Texas, United States)"
    nobody = fingerprint_row(dict(FORT_WORTH, Actor1Name="", EventRootCode="18"))
    assert nobody["headline"] == "Unidentified actor assaults (Fort Worth, Texas, United States)"
    protest = fingerprint_row(dict(FORT_WORTH, Actor1Name="ACTIVIST", Actor2Name="POLICE", EventRootCode="14"))
    assert protest["headline"] == "ACTIVIST protests against POLICE (Fort Worth, Texas, United States)"
    threat = fingerprint_row(dict(FORT_WORTH, Actor1Name="MEXICO", Actor2Name="CARTEL", EventRootCode="13"))
    assert threat["headline"].startswith("MEXICO threatens CARTEL")
    assert all(ch.isascii() for ch in row["summary"])


def test_cameo_roots_are_the_published_codes():
    assert CAMEO_ROOTS["14"][0] == "PROTEST"
    assert CAMEO_ROOTS["08"][0] == "YIELD" and CAMEO_ROOTS["09"][0] == "INVESTIGATE"  # were AID / YIELD
    assert sorted(CAMEO_ROOTS) == [f"{i:02d}" for i in range(1, 21)]


def test_source_text_is_the_url_words():
    assert source_text(FORT_WORTH["SOURCEURL"]) == "news 2024 jan 8 fort worth explosion debris scattered as authoriti"
    assert source_text(None) == ""


def test_generated_sql_takes_exactly_its_bound_parameters():
    # pymysql formats with %, so a stray % (DATE_FORMAT, LIKE) would raise here.
    insert_select_sql("e.SQLDATE BETWEEN %s AND %s") % ("'2024-01-01'", "'2024-01-31'")
    insert_select_sql("e.SQLDATE = %s") % ("'2024-01-09'",)
    for region_type in ("country", "state"):
        region_stats_sql(region_type) % (("'a'", "'b'") * 3)
    HOT_FINGERPRINTS_SQL % ("'a'", "'b'")


def test_region_stats_no_longer_use_alphabetical_max_as_primary_actor():
    sql = region_stats_sql("country")
    assert "MAX(Actor1Name)" not in sql and "ROW_NUMBER()" in sql


def test_region_stats_use_canonical_state_names():
    # GDELT stores "Yucatán" as "YucatáMX"; the stats must not key on that.
    sql = region_stats_sql("state")
    assert "WHEN 'YucatáMX' THEN 'Yucatán'" in sql and "top_event_ids" in sql
