"""Forecast evaluation: leak-free splits, seasonal-naive, honest baselines. numpy only."""
import numpy as np
import pytest

from thp_eval_utils import (
    apply_horizon_scale,
    compare_to_baselines,
    fit_horizon_scale,
    fit_log_interval,
    interval_coverage,
    log_interval_coverage,
    leak_free_split,
    max_train_target_day,
    per_series_win_rate,
    seasonal_naive,
    seasonal_naive_from_log_features,
)

HORIZON = 7


def positions(total_days=100, seq_len=14, horizon=HORIZON, series=3):
    """target_positions exactly as make_dataset builds them: start + seq_len."""
    one = np.arange(total_days - seq_len - horizon + 1) + seq_len
    return np.tile(one, series)


class TestLeakFreeSplit:
    def test_no_training_target_reaches_the_validation_period(self):
        pos = positions()
        s = leak_free_split(pos, 100, HORIZON, 0.15, 0.15)
        assert max_train_target_day(s, pos, HORIZON) < s.val_start

    def test_original_assignment_did_leak(self):
        """Documents the bug: assigning by first forecast day lets targets cross the cut."""
        pos = positions()
        cut = int(100 * 0.85)
        old_train = np.where(pos < cut)[0]
        assert (pos[old_train] + HORIZON - 1).max() >= cut

    def test_partitions_are_disjoint_and_ordered_in_time(self):
        pos = positions()
        s = leak_free_split(pos, 100, HORIZON, 0.15, 0.15)
        tr, va, te = (set(map(int, a)) for a in (s.train_idx, s.val_idx, s.test_idx))
        assert not (tr & va) and not (tr & te) and not (va & te)
        assert pos[s.train_idx].max() < pos[s.val_idx].min() <= pos[s.val_idx].max() < pos[s.test_idx].min()

    def test_validation_targets_stay_before_the_test_period(self):
        pos = positions()
        s = leak_free_split(pos, 100, HORIZON, 0.15, 0.15)
        assert (pos[s.val_idx] + HORIZON - 1).max() < s.test_start

    def test_straddling_windows_are_dropped_not_reassigned(self):
        pos = positions()
        s = leak_free_split(pos, 100, HORIZON, 0.15, 0.15)
        assert s.dropped_boundary_windows > 0
        assert sum(s.sizes()[k] for k in ("train", "val", "test")) + s.dropped_boundary_windows == len(pos)

    def test_test_fraction_zero_gives_no_test_set(self):
        s = leak_free_split(positions(), 100, HORIZON, 0.15, 0.0)
        assert len(s.test_idx) == 0

    def test_too_small_a_series_raises(self):
        with pytest.raises(RuntimeError):
            leak_free_split(positions(total_days=24), 24, HORIZON, 0.15, 0.15)


class TestSeasonalNaive:
    def test_repeats_the_same_weekday_from_last_week(self):
        hist = np.array([[10, 20, 30, 40, 50, 60, 70]], dtype=float)  # last 7 days
        out = seasonal_naive(hist, 7)
        assert out[0].tolist() == [10, 20, 30, 40, 50, 60, 70]

    def test_longer_horizon_cycles(self):
        hist = np.arange(1, 15, dtype=float).reshape(1, 14)
        out = seasonal_naive(hist, 10)
        assert out[0].tolist() == [8, 9, 10, 11, 12, 13, 14, 8, 9, 10]

    def test_matches_from_log_features(self):
        counts = np.random.default_rng(0).integers(0, 500, size=(5, 14)).astype(float)
        x = np.zeros((5, 14, 4), dtype=np.float32)
        x[:, :, 0] = np.log1p(counts)
        np.testing.assert_allclose(
            seasonal_naive_from_log_features(x, 7), seasonal_naive(counts, 7), rtol=1e-4, atol=1e-3
        )

    def test_needs_a_week_of_history(self):
        with pytest.raises(ValueError):
            seasonal_naive(np.ones((1, 5)), 7)

    def test_perfect_on_a_purely_weekly_series(self):
        week = np.array([5, 9, 9, 9, 9, 3, 2], dtype=float)
        series = np.tile(week, 4)
        hist, target = series[:14][None, :], series[14:21][None, :]
        assert np.abs(seasonal_naive(hist, 7) - target).sum() == 0


class TestHeadlineIsAgainstTheStrongestBaseline:
    def test_improvement_is_reported_against_the_best_baseline(self):
        y = np.full((10, 7), 100.0)
        weak, strong = np.full((10, 7), 200.0), np.full((10, 7), 120.0)
        model = np.full((10, 7), 110.0)
        r = compare_to_baselines(y, model, {"weak": weak, "strong": strong})
        assert r["strongest_baseline"] == "strong"
        assert r["improvement_pct_vs"]["weak"] == 90.0     # the flattering number
        assert r["improvement_pct_vs_strongest"] == 50.0   # the honest one

    def test_without_model_only_baselines_are_returned(self):
        r = compare_to_baselines(np.ones((2, 7)), None, {"a": np.zeros((2, 7))})
        assert "model_mae" not in r


class TestPerSeriesWinRate:
    def test_aggregate_win_can_hide_losing_most_series(self):
        # One huge series the model wins; nine small ones it loses.
        labels = np.array(["big"] * 5 + [f"s{i}" for i in range(9) for _ in range(5)])
        y = np.concatenate([np.full((5, 1), 1000.0), np.full((45, 1), 10.0)])
        base = np.concatenate([np.full((5, 1), 1500.0), np.full((45, 1), 10.0)])
        model = np.concatenate([np.full((5, 1), 1100.0), np.full((45, 1), 14.0)])
        agg_model, agg_base = np.abs(y - model).mean(), np.abs(y - base).mean()
        assert agg_model < agg_base                                   # looks like a win
        r = per_series_win_rate(labels, y, model, base)
        assert r["model_wins"] == 1 and r["win_rate"] == 0.1          # but wins 1 of 10


class TestIntervalCoverage:
    def test_coverage_of_the_validation_fitted_interval_on_new_data(self):
        rng = np.random.default_rng(1)
        pred = np.full((4000, 1), 100.0)
        val_res = rng.normal(0, 10, size=(4000, 1))
        q = [{"residual_q10": float(np.quantile(val_res, 0.1)), "residual_q90": float(np.quantile(val_res, 0.9))}]
        test_true = pred + rng.normal(0, 10, size=(4000, 1))
        assert abs(interval_coverage(test_true, pred, q)["overall"] - 0.80) < 0.03

    def test_coverage_collapses_if_the_distribution_shifts(self):
        pred = np.full((1000, 1), 100.0)
        q = [{"residual_q10": -5.0, "residual_q90": 5.0}]
        assert interval_coverage(pred + 50.0, pred, q)["overall"] == 0.0


class TestCalibration:
    def test_scale_fitted_on_one_split_corrects_a_biased_forecast(self):
        rng = np.random.default_rng(0)
        truth = rng.gamma(2.0, 50.0, size=(4000, 2))
        biased = truth * 0.8  # the model under-forecasts by 20%
        scales = fit_horizon_scale(truth[:2000], biased[:2000])
        assert scales == pytest.approx([1.25, 1.25], rel=1e-3)
        fixed = apply_horizon_scale(biased[2000:], scales)
        assert np.abs(fixed - truth[2000:]).mean() < 1e-3

    def test_log_interval_is_honest_across_scales_where_the_additive_one_is_not(self):
        # Two series sizes with the same relative noise. The additive interval is one width
        # for both, so it over-covers small series and under-covers large ones.
        rng = np.random.default_rng(1)
        level = np.where(rng.random(20000) < 0.5, 5.0, 5000.0)[:, None] * np.ones((1, 2))
        pred = level
        truth = level * np.exp(rng.normal(0, 0.3, size=level.shape))
        val, test = slice(0, 10000), slice(10000, None)
        log_q = fit_log_interval(truth[val], pred[val])
        cov = log_interval_coverage(truth[test], pred[test], log_q)
        assert cov["overall"] == pytest.approx(0.80, abs=0.02)

        resid = truth[val] - pred[val]
        additive = [{"residual_q10": float(np.quantile(resid[:, h], 0.1)),
                     "residual_q90": float(np.quantile(resid[:, h], 0.9))} for h in range(2)]
        small = level[test][:, 0] == 5.0
        for part in (small, ~small):
            log_cov = log_interval_coverage(truth[test][part], pred[test][part], log_q)["overall"]
            add_cov = interval_coverage(truth[test][part], pred[test][part], additive)["overall"]
            # log1p compresses residuals near zero, so small series still over-cover a little
            # (about 0.84 here), but far less wrong than the additive interval on either side.
            assert abs(log_cov - 0.80) < 0.05
            assert abs(log_cov - 0.80) < abs(add_cov - 0.80) / 2


def test_hawkes_weight_zero_is_the_ablation_and_default_is_unchanged():
    torch = pytest.importorskip("torch")
    from backend.services.thp_neural import NeuralTransformerHawkesModel

    kwargs = dict(input_size=4, seq_len=14, d_model=16, nhead=2, num_layers=1)
    assert NeuralTransformerHawkesModel(**kwargs).hawkes_residual_weight == 0.25  # old checkpoints
    torch.manual_seed(0)
    full = NeuralTransformerHawkesModel(**kwargs).eval()
    torch.manual_seed(0)
    ablated = NeuralTransformerHawkesModel(**kwargs, hawkes_residual_weight=0.0).eval()
    ablated.load_state_dict(full.state_dict())
    x = torch.randn(3, 14, 4)
    h = torch.arange(1, 8, dtype=torch.float32)
    pred_full, parts = full(x, h)
    pred_ablated, _ = ablated(x, h)
    # With the weight at zero the output is exactly the direct head.
    assert torch.allclose(pred_ablated, parts["direct"], atol=1e-6)
    assert not torch.allclose(pred_full, pred_ablated)


def _checkpoint(tmp_path, torch, **extra):
    """A tiny real checkpoint in the format train_thp_model.py writes."""
    from backend.services.thp_neural import FEATURE_SIZE, NeuralTransformerHawkesModel

    config = dict(input_size=FEATURE_SIZE, seq_len=14, d_model=16, nhead=2, num_layers=1)
    torch.manual_seed(0)
    model = NeuralTransformerHawkesModel(**config)
    path = tmp_path / "ckpt.pt"
    torch.save({
        "model_state": model.state_dict(), "config": config,
        "feature_mean": [0.0] * FEATURE_SIZE, "feature_std": [1.0] * FEATURE_SIZE,
        "target_mean": 0.5, "target_std": 1.0, "metadata": {}, **extra,
    }, path)
    return path


def test_residual_checkpoint_adds_last_weeks_log_count_at_inference(tmp_path):
    torch = pytest.importorskip("torch")
    from backend.services.thp_neural import FEATURE_SIZE, NeuralTHPCheckpoint

    rng = np.random.default_rng(0)
    window = rng.normal(size=(14, FEATURE_SIZE)).astype(np.float32)
    window[:, 0] = np.log1p(np.arange(14) * 10.0)  # feature 0 is log1p(count)
    plain = NeuralTHPCheckpoint(_checkpoint(tmp_path, torch)).predict(window.tolist(), 7)
    (tmp_path / "r").mkdir()
    residual = NeuralTHPCheckpoint(_checkpoint(tmp_path / "r", torch, target_mode="seasonal_residual")).predict(window.tolist(), 7)
    for h in range(7):
        gap = np.log1p(residual[h]["expected_events"]) - np.log1p(plain[h]["expected_events"])
        assert gap == pytest.approx(window[-7 + h, 0], abs=1e-4)  # same weekday last week


def test_served_interval_and_baseline_use_the_new_calibration(tmp_path):
    torch = pytest.importorskip("torch")
    from backend.services.thp_neural import NeuralTHPCheckpoint
    from backend.services.thp_service import TransformerHawkesForecaster

    log_q = [{"horizon": h + 1, "log_residual_q_lo": -0.2, "log_residual_q_hi": 0.3, "nominal": 0.8} for h in range(7)]
    service = TransformerHawkesForecaster.__new__(TransformerHawkesForecaster)
    service.neural_checkpoint = NeuralTHPCheckpoint(_checkpoint(
        tmp_path, torch, calibration={"horizon_scale": [1.0] * 7, "log_interval": log_q},
        metadata={"evaluation": {
            "baseline_improvement": {"moving_avg_7": {"baseline_mae": 167.7, "model_mae": 110.0, "mae_improvement_pct": 34.4}},
            "test": {"strongest_baseline": "seasonal_naive", "model_mae": 110.0, "improvement_pct_vs_strongest": -6.2,
                     "baseline_mae": {"seasonal_naive": 103.6, "moving_avg_7": 167.7}},
        }}))
    iv = service._prediction_interval(1000.0, 1)
    assert iv["low"] == pytest.approx(np.expm1(np.log1p(1000.0) - 0.2)) and iv["high"] == pytest.approx(np.expm1(np.log1p(1000.0) + 0.3))
    cmp = service._baseline_comparison()
    assert cmp["best_baseline"] == "seasonal_naive" and cmp["evaluated_on"] == "test" and cmp["mae_improvement_pct"] == -6.2


def test_served_interval_uses_the_series_size_bin(tmp_path):
    torch = pytest.importorskip("torch")
    from backend.services.thp_neural import NeuralTHPCheckpoint
    from backend.services.thp_service import TransformerHawkesForecaster

    narrow = [{"horizon": h + 1, "log_residual_q_lo": -0.1, "log_residual_q_hi": 0.1, "nominal": 0.8} for h in range(7)]
    wide = [{"horizon": h + 1, "log_residual_q_lo": -0.5, "log_residual_q_hi": 0.5, "nominal": 0.8} for h in range(7)]
    by_size = {"edges": [10.0, 100.0, 1000.0], "bins": {"0": wide, "1": wide, "2": narrow, "3": narrow}, "pooled": wide}
    service = TransformerHawkesForecaster.__new__(TransformerHawkesForecaster)
    service.neural_checkpoint = NeuralTHPCheckpoint(_checkpoint(tmp_path, torch, calibration={"log_interval_by_size": by_size}))
    small = service._prediction_interval(8.0, 1, window_mean=5.0)
    large = service._prediction_interval(500.0, 1, window_mean=400.0)
    assert small["high"] == pytest.approx(np.expm1(np.log1p(8.0) + 0.5))
    assert large["high"] == pytest.approx(np.expm1(np.log1p(500.0) + 0.1))
