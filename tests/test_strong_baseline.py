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
