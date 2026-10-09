"""Moving-block bootstrap for an MAE difference over one test period. numpy only."""
import numpy as np
import pytest

from thp_eval_utils import moving_block_bootstrap_ci


def test_a_constant_difference_has_a_zero_width_interval():
    r = moving_block_bootstrap_ci(np.full(66, -5.0), block=7, n_resamples=500, seed=1)
    assert r["mean"] == r["lo"] == r["hi"] == pytest.approx(-5.0)
    assert r["days"] == 66 and r["block_days"] == 7


def test_the_interval_contains_the_mean_and_is_reproducible():
    rng = np.random.default_rng(0)
    diff = -3.0 + rng.normal(0, 4, 66)
    a = moving_block_bootstrap_ci(diff, seed=7)
    b = moving_block_bootstrap_ci(diff, seed=7)
    assert a == b
    assert a["lo"] < a["mean"] < a["hi"]


def test_dependent_days_widen_the_interval_compared_with_single_days():
    # A slow wave (runs of good and bad weeks): resampling single days treats them as independent
    # and is too narrow; 7-day blocks keep the runs together.
    days = np.arange(70)
    diff = 5.0 * np.sign(np.sin(days / 7.0 * np.pi / 2))
    single = moving_block_bootstrap_ci(diff, block=1, n_resamples=2000, seed=3)
    weekly = moving_block_bootstrap_ci(diff, block=7, n_resamples=2000, seed=3)
    assert (weekly["hi"] - weekly["lo"]) > (single["hi"] - single["lo"])


def test_too_few_days_is_an_error():
    with pytest.raises(ValueError):
        moving_block_bootstrap_ci(np.zeros(5), block=7)


def test_per_day_mae_averages_every_series_and_horizon_day_of_a_start_day():
    from evaluate_strong_baseline import per_day_mae
    y = np.array([[10.0, 10.0], [20.0, 20.0], [5.0, 5.0]])
    pred = np.array([[12.0, 8.0], [20.0, 24.0], [5.0, 5.0]])
    pos = np.array([3, 3, 4])  # two windows start on day 3, one on day 4
    assert per_day_mae(y, pred, pos, np.array([3, 4])).tolist() == [(2 + 2 + 0 + 4) / 4, 0.0]


def test_interval_method_rule_is_the_transformers_worst_bin_gap_then_width():
    from evaluate_strong_baseline import choose_interval_method
    # The LightGBM validation scores: rolling 14 days has the smallest worst-bin gap.
    scores = {"static": (0.042, 223.6), "rolling_14d": (0.029, 223.5), "rolling_28d": (0.038, 224.1)}
    assert choose_interval_method(scores) == "rolling_14d"
    assert choose_interval_method({"a": (0.03, 230.0), "b": (0.03, 220.0)}) == "b"  # tie: narrower
