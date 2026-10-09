"""The report checks themselves, on hand-written good and bad reports. No LLM, no network."""
from answer_quality import (
    check_report,
    comparison_direction_ok,
    dates_outside,
    sample_as_total,
    ungrounded_numbers,
)

COMPARE_DATA = {"compare_periods_0": {"data": {
    "earlier": {"label": "February 2024", "event_count": 858, "events_per_day": 29.59},
    "later": {"label": "March 2024", "event_count": 1193, "events_per_day": 38.48},
    "absolute_change": 335, "percent_change_per_day": 30.1, "direction": "increase",
}}}
COMPARE_PLAN = {"steps": [{"type": "compare_periods", "params": {"periods": [
    {"start": "2024-02-01", "end": "2024-02-29"}, {"start": "2024-03-01", "end": "2024-03-31"}]}}]}

EVENTS_DATA = {"events_0": {"data": [
    {"SQLDATE": "2024-01-09", "NumArticles": 120, "ActionGeo_FullName": "Fort Worth, Texas, United States"},
    {"SQLDATE": "2024-01-10", "NumArticles": 70, "ActionGeo_FullName": "Austin, Texas, United States"},
]}}
EVENTS_PLAN = {"steps": [{"type": "events", "params": {"start_date": "2024-01-01", "end_date": "2024-01-31"}}]}


def test_a_faithful_comparison_report_passes():
    text = ("Protest events in Canada rose from 858 in February 2024 to 1,193 in March 2024, "
            "an increase of 30.1% per day (29.59 to 38.48 events per day).")
    assert check_report(text, COMPARE_PLAN, COMPARE_DATA)["pass"]


def test_invented_numbers_are_caught():
    text = "Protests rose 45% to about 1,400 events in March."
    assert ungrounded_numbers(text, COMPARE_DATA) == ["45%", "1,400"]


def test_the_wrong_direction_is_caught():
    assert comparison_direction_ok("Protest activity fell in March.", COMPARE_DATA["compare_periods_0"]["data"]) is False
    assert comparison_direction_ok("Activity rose in March.", COMPARE_DATA["compare_periods_0"]["data"]) is True


def test_totals_and_trends_from_a_top_n_sample_are_caught():
    assert sample_as_total("In total there were 120 protests across Texas.", ["events"])
    assert sample_as_total("Protests increased by 40% over the month.", ["events"])
    assert sample_as_total("The Fort Worth explosion drew 120 articles.", ["events"]) == []
    # With a count step present, totals are allowed (they come from the counts).
    assert sample_as_total("In total there were 858 events.", ["compare_periods"]) == []


def test_dates_outside_the_queried_window_are_caught():
    assert dates_outside("On 2024-01-09 and February 3, 2024 ...", "2024-01-01", "2024-01-31") == ["February 3, 2024"]
    assert dates_outside("On January 9, the explosion ...", "2024-01-01", "2024-01-31") == []


def test_dates_and_years_are_not_counted_as_numbers():
    text = "On January 9, 2024 (2024-01-09) the Fort Worth explosion drew 120 articles."
    assert ungrounded_numbers(text, EVENTS_DATA) == []
    assert check_report(text, EVENTS_PLAN, EVENTS_DATA)["pass"]


def test_false_positives_seen_in_the_first_live_run():
    # "December 23-29, 2024" is a date range, not the number 29.
    assert ungrounded_numbers("Protests during the week of December 23-29, 2024 drew 120 articles.", EVENTS_DATA) == []
    # "December 2024" is a month, not December 20; "30 December 2024" is a day-first date.
    assert dates_outside("Texas on 30 December 2024; activity in December 2024 ...", "2024-12-30", "2024-12-30") == []
    assert dates_outside("Earlier, on 20 December 2024 ...", "2024-12-30", "2024-12-30") == ["20 December 2024"]
    assert dates_outside("the week of December 23-29, 2024", "2024-12-23", "2024-12-27") == ["December 23-29, 2024"]


def test_qualitative_trend_claims_from_a_sample_are_caught():
    from answer_quality import qualitative_trend

    # The sentence the first live run let through (month-end-03, a top-50 sample).
    assert qualitative_trend("Coverage rose toward the end of the month.", ["events"])
    assert qualitative_trend("Protest activity picked up in the second half.", ["top_events"])
    # Not claims: hedged, negated, or a date.
    for ok in ("The data has no period comparison, so it cannot show whether activity rose or fell.",
               "I can't say whether protests increased.",
               "The sharpest event of the week also fell on that day, with 10 articles."):
        assert qualitative_trend(ok, ["events"]) == [], ok
    # A comparison step makes trends legitimate.
    assert qualitative_trend("Protests rose in March.", ["compare_periods"]) == []


def test_causal_candidates_keep_guesses_and_drop_statements_of_what_the_data_cannot_show():
    from answer_quality import causal_candidates

    # Sentences from the 2026-10-09 reports that read like findings the records do not contain.
    for claim in ("That is the most negative of the sampled events, and it points to a judicial dispute.",
                  "Robb Elementary was the site of the 2022 shooting, so the pairing suggests ongoing attention to that case.",
                  # a trailing limit clause does not cancel the guess before it
                  "Read together, the events suggest a confrontation, though the underlying detail isn't in the records."):
        assert causal_candidates(claim) == [claim], claim
    for fine in ("The data does not say what happened at any of them, and nothing in it establishes a causal connection.",
                 "The data doesn't say what prompted this cluster, so the underlying incident stays unclear.",
                 "I also can't say whether the day was unusual, because the brief has no comparison period.",
                 "Protests in Austin drew 70 articles on 2024-01-10."):
        assert causal_candidates(fine) == [], fine


def test_sentences_do_not_split_after_vs_or_mid_sentence_initialisms():
    from answer_quality import sentences

    text = ("An event coded as Canada vs. United States drew 96 articles. Most were tied to U.S. political "
            "disputes. It centers on Washington, D.C. It drew 150 articles; one was the only non-U.S. location.")
    assert sentences(text) == [
        "An event coded as Canada vs. United States drew 96 articles.",
        "Most were tied to U.S. political disputes.",
        "It centers on Washington, D.C.",
        "It drew 150 articles; one was the only non-U.S. location.",
    ]


PERRY = {"similar_events_0": {"type": "similar_events", "data": [
    {"SQLDATE": "2024-01-05", "NumArticles": n, "Actor1Name": "POLICE", "ActionGeo_FullName": loc}
    for n, loc in ((70, "Perry High School, Iowa, United States"),) * 3 + ((60, "Maine, United States"),)
] + [{"SQLDATE": "2024-01-06", "NumArticles": 100, "Actor1Name": "AUTHORITIES"}]}}


def test_counts_written_as_words_are_checked():
    from answer_quality import count_overclaims

    # detail-01 (2026-10-09): the data has three such records, the report said four.
    bad = "At Perry High School on 2024-01-05, four records, each with 70 articles, involve POLICE."
    assert count_overclaims(bad, PERRY) == [bad]
    assert not check_report(bad, {"steps": [{"type": "similar_events"}]}, PERRY)["pass"]
    for ok in ("At Perry High School on 2024-01-05, three records, each with 70 articles, involve POLICE.",
               "Two records on 2024-01-05 drew 70 articles each.",        # a subset is fine
               "Two records on 2024-01-05 drew 60 to 70 articles.",       # a range
               "On 2024-01-05 two records drew 70 and 60 articles.",      # two counts: either
               "Of the five records, four are dated 2024-01-05.",         # "of the five" is the list
               "Four records were listed in total."):                       # nothing to check against
        assert count_overclaims(ok, PERRY) == [], ok
    # The day of a date is not the count, and does not hide the count that follows it.
    assert count_overclaims("On January 6 three events drew 100 articles.", PERRY)
    assert count_overclaims("On January 6 one event drew 100 articles.", PERRY) == []
