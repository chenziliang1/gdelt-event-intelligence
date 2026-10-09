"""
Precomputed tables (event_fingerprints, region_daily_stats, daily_summary hot lists), defined
once and used by the daily ETL (``etl_pipeline.py``) and by the backfill (``backfill_precompute.py``).

Rows are computed in SQL (INSERT ... SELECT) so 17.5M events take minutes, not the days the
row-by-row Python insert needed. The fingerprint SQL is generated from the tables below, and
``fingerprint_row`` computes the same row in Python: ``backfill_precompute.py --verify``
compares the two on a sample of real rows, and ``tests/test_precompute_sql.py`` covers the
Python side.

Fixes relative to the original ETL:

* Fingerprints ended in the last 3 digits of GlobalEventID, so two events on the same day,
  place and type could collide on the UNIQUE fingerprint and the second was silently dropped.
  They now end in the full GlobalEventID.
* Headlines and summaries were mangled machine translations ("sendtablesoundclear").
  They are plain English built from the CAMEO root code (the root map also had 05-09 wrong).
* GDELT has no article titles. ``source_text`` holds the words of the source URL path
  ("fort worth explosion debris scattered ..."), the only article text available, and is
  what keyword search matches (FULLTEXT on headline + source_text).
* The location code is letters only, so fingerprints match the planner's ID pattern
  ("ST." from "St. Louis" did not).

Template text, not LLM output: ``llm_version`` is ``TEMPLATE_VERSION``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.queries.geo_names import adm1_case_sql  # noqa: E402  (six damaged Mexican state names)

TEMPLATE_VERSION = "template-v2"

# CAMEO root code -> (fingerprint token, label, verb, preposition before Actor2).
# An empty preposition means Actor2 is the direct object ("threatens X").
CAMEO_ROOTS: Dict[str, Tuple[str, str, str, str]] = {
    "01": ("STATEMENT", "Public statement", "makes a public statement", "about"),
    "02": ("APPEAL", "Appeal", "appeals", "to"),
    "03": ("INTENT", "Intent to cooperate", "expresses intent to cooperate", "with"),
    "04": ("CONSULT", "Consultation", "consults", "with"),
    "05": ("DIPLOMACY", "Diplomatic cooperation", "cooperates diplomatically", "with"),
    "06": ("COOPERATE", "Material cooperation", "cooperates materially", "with"),
    "07": ("AID", "Aid", "provides aid", "to"),
    "08": ("YIELD", "Yield", "yields", "to"),
    "09": ("INVESTIGATE", "Investigation", "investigates", ""),
    "10": ("DEMAND", "Demand", "makes demands", "of"),
    "11": ("DISAPPROVE", "Disapproval", "criticizes", ""),
    "12": ("REJECT", "Rejection", "rejects", ""),
    "13": ("THREATEN", "Threat", "threatens", ""),
    "14": ("PROTEST", "Protest", "protests", "against"),
    "15": ("FORCE", "Show of force", "exhibits force", "toward"),
    "16": ("REDUCE", "Reduced relations", "reduces relations", "with"),
    "17": ("COERCE", "Coercion", "coerces", ""),
    "18": ("ASSAULT", "Assault", "assaults", ""),
    "19": ("FIGHT", "Fighting", "fights", ""),
    "20": ("MASSVIOLENCE", "Mass violence", "uses mass violence", "against"),
}
DEFAULT_ROOT = ("EVENT", "Other event", "interacts", "with")
UNKNOWN_ACTOR = "Unidentified actor"


# ---------------------------------------------------------------------------
# Python reference implementation
# ---------------------------------------------------------------------------

def location_code(location: Optional[str]) -> str:
    letters = re.sub(r"[^A-Z]", "", (location or "").split(",")[0].upper())
    return letters[:3] or "UNK"


def source_text(url: Optional[str]) -> str:
    path = re.sub(r"^https?://[^/]+", "", url or "", flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", " ", path.lower()).strip()


def severity(goldstein: Optional[float], articles: Optional[int]) -> float:
    score = min(10.0, max(1.0, abs(goldstein or 0.0) * 2))
    if (articles or 0) > 100:
        score += 1
    return min(10.0, score)


def fingerprint_row(evt: Dict[str, Any]) -> Dict[str, Any]:
    """The row the SQL produces for one events_table row (used by tests)."""
    token, label, verb, prep = CAMEO_ROOTS.get(str(evt.get("EventRootCode") or "")[:2], DEFAULT_ROOT)
    a1 = evt.get("Actor1Name") or ""
    a2 = evt.get("Actor2Name") or ""
    loc = evt.get("ActionGeo_FullName") or ""
    country = evt.get("ActionGeo_CountryCode") or "XX"
    day = str(evt["SQLDATE"]).replace("-", "")

    head = f"{a1 or UNKNOWN_ACTOR} {verb}"
    if a2:
        head += f" {prep} {a2}" if prep else f" {a2}"
    if loc:
        head += f" ({loc})"

    articles = evt.get("NumArticles") or 0
    summary = f"{label} involving {', '.join(a for a in (a1, a2) if a) or 'unidentified actors'}"
    if loc:
        summary += f" in {loc}"
    summary += f", Goldstein {float(evt.get('GoldsteinScale') or 0):.1f}, {articles} articles."

    return {
        "global_event_id": evt["GlobalEventID"],
        "fingerprint": f"{country}-{day}-{location_code(loc)}-{token}-{evt['GlobalEventID']}",
        "headline": head[:255],
        "summary": summary,
        "key_actors": [a for a in (a1, a2) if a],
        "event_type_label": label,
        "severity_score": severity(evt.get("GoldsteinScale"), articles),
        "location_name": loc[:100],
        "location_country": country,
        "source_text": source_text(evt.get("SOURCEURL")),
        "llm_version": TEMPLATE_VERSION,
    }


# ---------------------------------------------------------------------------
# SQL generated from the same tables
# ---------------------------------------------------------------------------

def _sql_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _root_case(field_index: int) -> str:
    whens = " ".join(
        f"WHEN {_sql_str(code)} THEN {_sql_str(parts[field_index])}" for code, parts in CAMEO_ROOTS.items()
    )
    return f"(CASE LEFT(e.EventRootCode, 2) {whens} ELSE {_sql_str(DEFAULT_ROOT[field_index])} END)"


def insert_select_sql(where: str) -> str:
    """INSERT ... SELECT for the events matching ``where`` (bind values with %s).

    Idempotent: re-running it rewrites the derived columns of existing rows.
    """
    token, label, verb, prep = (_root_case(i) for i in range(4))
    a1 = "NULLIF(e.Actor1Name, '')"
    a2 = "NULLIF(e.Actor2Name, '')"
    loc = "NULLIF(e.ActionGeo_FullName, '')"
    loc_code = (
        "COALESCE(NULLIF(LEFT(REGEXP_REPLACE(UPPER(SUBSTRING_INDEX(COALESCE(e.ActionGeo_FullName, ''), ',', 1)),"
        " '[^A-Z]', ''), 3), ''), 'UNK')"
    )
    headline = (
        f"LEFT(CONCAT(COALESCE({a1}, {_sql_str(UNKNOWN_ACTOR)}), ' ', {verb},"
        f" IF({a2} IS NULL, '', IF({prep} = '', CONCAT(' ', {a2}), CONCAT(' ', {prep}, ' ', {a2}))),"
        f" IF({loc} IS NULL, '', CONCAT(' (', {loc}, ')'))), 255)"
    )
    summary = (
        f"CONCAT({label}, ' involving ', COALESCE(NULLIF(CONCAT_WS(', ', {a1}, {a2}), ''), 'unidentified actors'),"
        f" IF({loc} IS NULL, '', CONCAT(' in ', {loc})),"
        " ', Goldstein ', FORMAT(COALESCE(e.GoldsteinScale, 0), 1), ', ', COALESCE(e.NumArticles, 0), ' articles.')"
    )
    key_actors = (
        f"(CASE WHEN {a1} IS NOT NULL AND {a2} IS NOT NULL THEN JSON_ARRAY({a1}, {a2})"
        f" WHEN {a1} IS NOT NULL THEN JSON_ARRAY({a1}) WHEN {a2} IS NOT NULL THEN JSON_ARRAY({a2})"
        " ELSE JSON_ARRAY() END)"
    )
    severity_sql = (
        "LEAST(10, LEAST(10, GREATEST(1, ABS(COALESCE(e.GoldsteinScale, 0)) * 2))"
        " + IF(COALESCE(e.NumArticles, 0) > 100, 1, 0))"
    )
    source = (
        "TRIM(REGEXP_REPLACE(LOWER(REGEXP_REPLACE(COALESCE(e.SOURCEURL, ''), '^https?://[^/]+', '')),"
        " '[^a-z0-9]+', ' '))"
    )
    return f"""
INSERT INTO event_fingerprints
    (global_event_id, fingerprint, headline, summary, key_actors, event_type_label,
     severity_score, location_name, location_country, source_text, llm_version)
SELECT
    e.GlobalEventID,
    CONCAT(COALESCE(NULLIF(e.ActionGeo_CountryCode, ''), 'XX'), '-', DATE_FORMAT(e.SQLDATE, '%%Y%%m%%d'), '-',
           {loc_code}, '-', {token}, '-', e.GlobalEventID),
    {headline},
    {summary},
    {key_actors},
    {label},
    {severity_sql},
    LEFT(COALESCE(e.ActionGeo_FullName, ''), 100),
    COALESCE(NULLIF(e.ActionGeo_CountryCode, ''), 'XX'),
    {source},
    {_sql_str(TEMPLATE_VERSION)}
FROM events_table e
WHERE {where}
ON DUPLICATE KEY UPDATE
    fingerprint = VALUES(fingerprint), headline = VALUES(headline), summary = VALUES(summary),
    key_actors = VALUES(key_actors), event_type_label = VALUES(event_type_label),
    severity_score = VALUES(severity_score), location_name = VALUES(location_name),
    location_country = VALUES(location_country), source_text = VALUES(source_text),
    llm_version = VALUES(llm_version)
"""


# daily_summary.hot_event_fingerprints: the day's top events by NumArticles x |Goldstein|,
# as fingerprints. Run after the fingerprints for those days exist.
HOT_FINGERPRINTS_SQL = """
UPDATE daily_summary d
JOIN (
    -- JSON_ARRAYAGG has no ORDER BY; fingerprints are [A-Z0-9-] only, so this is valid JSON.
    SELECT day, CAST(CONCAT('["', GROUP_CONCAT(fingerprint ORDER BY rn SEPARATOR '","'), '"]') AS JSON) AS fps
    FROM (
        SELECT e.SQLDATE AS day, f.fingerprint,
               ROW_NUMBER() OVER (PARTITION BY e.SQLDATE
                                  ORDER BY e.NumArticles * ABS(e.GoldsteinScale) DESC, e.GlobalEventID) AS rn
        FROM events_table e
        JOIN event_fingerprints f ON f.global_event_id = e.GlobalEventID
        WHERE e.SQLDATE BETWEEN %s AND %s
    ) ranked
    WHERE rn <= 20
    GROUP BY day
) h ON h.day = d.date
SET d.hot_event_fingerprints = h.fps
"""


def parse_key_actors(value: Any) -> list:
    return json.loads(value) if isinstance(value, str) else list(value or [])


# ---------------------------------------------------------------------------
# region_daily_stats
# ---------------------------------------------------------------------------
# One row per (region, day) for countries and for first-level divisions (US states, Canadian
# provinces, Mexican states). GDELT ActionGeo_Type 2-5 names end in "<ADM1>, <Country>", so the
# ADM1 name is the second-to-last comma part. region_code is the upper-cased name for
# divisions ("TEXAS"), the FIPS code for countries ("US"); region_name is the readable name.
#
# The original ETL used MAX(Actor1Name) and MAX(ActionGeo_FullName), i.e. the alphabetically
# last values, as "primary actor" and region name. Both are now the most frequent / the
# country-level name. Counts use the same thresholds as everywhere else (Goldstein < -5 / > 5).

_RAW_ADM1 = "TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(e.ActionGeo_FullName, ',', -2), ',', 1))"
# Canonical name ("Yucatán", not GDELT's "YucatáMX"). region_code is its upper case; lookups
# compare under the table's accent-insensitive collation, so "YUCATAN" finds "YUCATÁN".
_ADM1 = adm1_case_sql(_RAW_ADM1)

_REGION_KEYS = {
    "country": ("e.ActionGeo_CountryCode", "e.ActionGeo_CountryCode <> ''"),
    # '%%' because these strings are executed with bound parameters.
    "state": (f"UPPER({_ADM1})", "e.ActionGeo_Type IN (2, 3, 4, 5) AND e.ActionGeo_FullName LIKE '%%,%%'"),
}


def region_stats_sql(region_type: str) -> str:
    """INSERT ... SELECT for one region type; bind (start, end) three times."""
    key, where = _REGION_KEYS[region_type]
    name = (
        "COALESCE(MAX(CASE WHEN e.ActionGeo_Type = 1 THEN e.ActionGeo_FullName END), e.ActionGeo_CountryCode)"
        if region_type == "country"
        else f"MAX({_ADM1})"
    )
    return f"""
INSERT INTO region_daily_stats
    (region_code, region_name, region_type, date, event_count, conflict_events, cooperation_events,
     avg_goldstein, conflict_intensity, cooperation_intensity, avg_tone, primary_actors, top_event_ids)
SELECT s.code, s.name, '{region_type}', s.day, s.n, s.conflicts, s.cooperation, s.avg_goldstein,
       s.conflict_intensity, s.cooperation_intensity, s.avg_tone,
       COALESCE(a.actors, JSON_ARRAY()), COALESCE(t.ids, JSON_ARRAY())
FROM (
    SELECT {key} AS code, {name} AS name, e.SQLDATE AS day, COUNT(*) AS n,
           SUM(e.GoldsteinScale < -5) AS conflicts, SUM(e.GoldsteinScale > 5) AS cooperation,
           AVG(e.GoldsteinScale) AS avg_goldstein,
           AVG(CASE WHEN e.GoldsteinScale < 0 THEN -e.GoldsteinScale ELSE 0 END) AS conflict_intensity,
           AVG(CASE WHEN e.GoldsteinScale > 0 THEN e.GoldsteinScale ELSE 0 END) AS cooperation_intensity,
           AVG(e.AvgTone) AS avg_tone
    FROM events_table e
    WHERE e.SQLDATE BETWEEN %s AND %s AND {where}
    GROUP BY code, day
) s
LEFT JOIN (
    SELECT code, day,
           CASE WHEN MAX(rn) >= 3 THEN JSON_ARRAY(MAX(IF(rn = 1, obj, NULL)), MAX(IF(rn = 2, obj, NULL)), MAX(IF(rn = 3, obj, NULL)))
                WHEN MAX(rn) = 2 THEN JSON_ARRAY(MAX(IF(rn = 1, obj, NULL)), MAX(IF(rn = 2, obj, NULL)))
                ELSE JSON_ARRAY(MAX(IF(rn = 1, obj, NULL))) END AS actors
    FROM (
        SELECT code, day, JSON_OBJECT('name', actor, 'count', c) AS obj,
               ROW_NUMBER() OVER (PARTITION BY code, day ORDER BY c DESC, actor) AS rn
        FROM (
            SELECT {key} AS code, e.SQLDATE AS day, e.Actor1Name AS actor, COUNT(*) AS c
            FROM events_table e
            WHERE e.SQLDATE BETWEEN %s AND %s AND {where} AND e.Actor1Name <> ''
            GROUP BY code, day, actor
        ) counts
    ) ranked
    WHERE rn <= 3
    GROUP BY code, day
) a ON a.code = s.code AND a.day = s.day
LEFT JOIN (
    -- The day's 5 most-covered events: the top 5 of any date range are among these, so the
    -- overview's hot events need no scan of the range.
    SELECT code, day, CAST(CONCAT('[', GROUP_CONCAT(gid ORDER BY rn), ']') AS JSON) AS ids
    FROM (
        SELECT {key} AS code, e.SQLDATE AS day, e.GlobalEventID AS gid,
               ROW_NUMBER() OVER (PARTITION BY {key}, e.SQLDATE ORDER BY e.NumArticles DESC, e.GlobalEventID) AS rn
        FROM events_table e
        WHERE e.SQLDATE BETWEEN %s AND %s AND {where}
    ) ranked
    WHERE rn <= 5
    GROUP BY code, day
) t ON t.code = s.code AND t.day = s.day
WHERE s.code IS NOT NULL AND s.code <> ''
ON DUPLICATE KEY UPDATE
    region_name = VALUES(region_name), region_type = VALUES(region_type), event_count = VALUES(event_count),
    conflict_events = VALUES(conflict_events), cooperation_events = VALUES(cooperation_events),
    avg_goldstein = VALUES(avg_goldstein), conflict_intensity = VALUES(conflict_intensity),
    cooperation_intensity = VALUES(cooperation_intensity), avg_tone = VALUES(avg_tone),
    primary_actors = VALUES(primary_actors), top_event_ids = VALUES(top_event_ids)
"""


# Columns the precompute SQL needs beyond the original precompute_tables.sql schema.
# Applied by backfill_precompute.py (MySQL has no ADD COLUMN IF NOT EXISTS).
SCHEMA_UPGRADES = {
    ("event_fingerprints", "source_text"): "ALTER TABLE event_fingerprints ADD COLUMN source_text TEXT",
    ("region_daily_stats", "conflict_events"): "ALTER TABLE region_daily_stats ADD COLUMN conflict_events INT DEFAULT 0",
    ("region_daily_stats", "cooperation_events"): "ALTER TABLE region_daily_stats ADD COLUMN cooperation_events INT DEFAULT 0",
    ("region_daily_stats", "avg_goldstein"): "ALTER TABLE region_daily_stats ADD COLUMN avg_goldstein FLOAT",
    ("region_daily_stats", "top_event_ids"): "ALTER TABLE region_daily_stats ADD COLUMN top_event_ids JSON",
}
# Location search on state names: an indexed virtual column instead of LIKE '%, Texas%' over the
# full ActionGeo_FullName text, which scanned every row of the date range (12.7 s for a state-year).
ADM1_COLUMN = (
    "ALTER TABLE events_table ADD COLUMN ActionGeo_ADM1 VARCHAR(100) AS "
    "(TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(ActionGeo_FullName, ',', -2), ',', 1))) VIRTUAL"
)
ADM1_INDEX = (
    "ALTER TABLE events_table ADD INDEX idx_adm1_date_root_articles "
    "(ActionGeo_ADM1, SQLDATE, EventRootCode, NumArticles)"
)

# region_code was VARCHAR(10): too short for "NORTH CAROLINA".
WIDEN_REGION_CODE = "ALTER TABLE region_daily_stats MODIFY region_code VARCHAR(64) NOT NULL"
FULLTEXT_INDEX = "ALTER TABLE event_fingerprints ADD FULLTEXT INDEX ft_headline_source (headline, source_text)"
