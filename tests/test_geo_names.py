"""Place-name handling for GDELT's damaged Mexican state names. No database."""
from backend.queries.core_queries import _build_smart_location_condition
from backend.queries.geo_names import canonical_adm1, fold, stored_adm1_spellings


def test_damaged_spellings_map_to_canonical_names():
    assert canonical_adm1("YucatáMX") == "Yucatán"
    assert canonical_adm1("Méco") == "Estado de México"
    assert canonical_adm1("Texas") == "Texas"


def test_users_can_type_the_name_with_or_without_accents():
    for typed in ("Yucatán", "Yucatan", "yucatan"):
        assert stored_adm1_spellings(typed) == ["YucatáMX"]
    assert stored_adm1_spellings("Nuevo Leon") == ["Nuevo LeóX"]
    assert stored_adm1_spellings("Michoacán") == ["Michoacáde Ocampo"]
    assert stored_adm1_spellings("Mexico") == []  # the country, not the state
    assert stored_adm1_spellings("Texas") == []
    assert fold("Querétaro") == "QUERETARO"


def test_search_matches_city_level_events_and_damaged_state_spellings():
    # States go through the indexed ActionGeo_ADM1 column, which holds both "Texas, United States"
    # and "Austin, Texas, United States" rows; a prefix-only LIKE missed the city rows.
    sql, params = _build_smart_location_condition("Yucatan", None)
    assert "ActionGeo_ADM1 IN" in sql and params == ["Yucatán", "YucatáMX"]
    sql, params = _build_smart_location_condition("Texas", None)
    assert sql == " AND (ActionGeo_ADM1 IN (%s))" and params == ["Texas"]
    sql, params = _build_smart_location_condition("Canada", None)
    assert params == ["CA"] and "ActionGeo_CountryCode" in sql
    sql, params = _build_smart_location_condition("Toronto", None)
    assert params == ["Toronto%"]


def test_known_locations_cover_canada_and_mexico_and_avoid_false_places():
    from backend.agents.known_locations import find_known_location

    assert find_known_location("protests in Toronto last week") == "Toronto"
    assert find_known_location("situation in Ontario") == "Ontario"
    assert find_known_location("events in Monterrey") == "Monterrey"
    assert find_known_location("what happened in yucatan") == "Yucatán"
    assert find_known_location("Nuevo Leon violence") == "Nuevo León"
    assert find_known_location("tell us something") is None  # not the US
    assert find_known_location("news about BlackRock") is None  # an organisation geocoded as a place


def test_search_inner_query_uses_the_city_level_match():
    from backend.queries.core_queries import _build_optimized_search_sql

    sql, params = _build_optimized_search_sql("2024-03-01", "2024-03-31", "Texas", None, "protest", None, None, None, 20)
    assert "ActionGeo_ADM1 IN" in sql and "Texas" in params  # was "Texas%": 287 of 612 Texas protests in March 2024


def test_multi_word_places_are_not_split_into_words():
    from backend.queries.query_utils import parse_region_input

    assert "New" not in parse_region_input("New York") and "New York" in parse_region_input("New York")
    assert "Los" not in parse_region_input("Los Angeles")
    terms = parse_region_input("Estado de México")
    assert "de" not in terms and "México" not in terms
    assert {"Texas", "Ontario"} <= set(parse_region_input("Texas, Ontario"))  # explicit lists still split
    sql, params = _build_smart_location_condition("Estado de México", None)
    assert params == ["Estado de México", "Méco"]  # was also 'Estado%', 'MX', 'de%', 'DE'
