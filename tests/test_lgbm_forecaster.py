"""The served LightGBM forecaster: feature rows match the evaluation script; the service picks the model by path.

The prediction-level check (serving path vs the evaluation script's predictions on test windows, max
difference 0.0) runs in `evaluate_strong_baseline.py --stage serve`, which needs lightgbm and the cache.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from backend.services.lgbm_forecaster import HORIZON, LightGBMCheckpoint, lgbm_feature_rows


def test_feature_rows_match_the_evaluation_script():
    import evaluate_strong_baseline as esb

    rng = np.random.default_rng(0)
    labels = np.asarray(["country:CA||protest", "actor:POLICE||all", "global:ALL||conflict"])
    d = {"x": rng.normal(size=(3, 14, 16)).astype(np.float32), "labels": labels,
         "target_positions": np.asarray([10, 11, 40])}
    day0 = date(2024, 1, 1)
    X, names, _, _ = esb.features(d, np.arange(3), day0, series_id=False)
    kinds = {k: i for i, k in enumerate(sorted({s.split(":", 1)[0] for s in labels}))}
    measures = {m: i for i, m in enumerate(sorted({s.split("||", 1)[1] for s in labels}))}
    for i, label in enumerate(labels):
        dow = (day0 + timedelta(days=int(d["target_positions"][i]))).weekday()
        rows = lgbm_feature_rows(d["x"][i], kinds[label.split(":", 1)[0]], measures[label.split("||", 1)[1]], dow)
        assert np.array_equal(rows, X[i * HORIZON:(i + 1) * HORIZON]), label
    assert names[29] == "seasonal_log"  # the column the served prediction adds back


def test_a_missing_model_is_reported_not_raised(tmp_path):
    ck = LightGBMCheckpoint(tmp_path / "missing.json")
    assert not ck.available and "not found" in ck.error
    assert ck.predict([[0.0] * 16] * 14, 7, "global:ALL", "all", date(2024, 12, 31)) is None


def test_the_service_serves_lightgbm_by_default_and_the_transformer_for_a_pt(monkeypatch):
    from backend.services import thp_service

    monkeypatch.delenv("FORECAST_MODEL_PATH", raising=False)
    assert isinstance(thp_service.TransformerHawkesForecaster().neural_checkpoint, LightGBMCheckpoint)
    monkeypatch.setenv("FORECAST_MODEL_PATH", "models/thp_gdelt.pt")
    from backend.services.thp_neural import NeuralTHPCheckpoint
    assert isinstance(thp_service.TransformerHawkesForecaster().neural_checkpoint, NeuralTHPCheckpoint)


def test_served_model_predicts_seven_days_with_intervals():
    try:
        import lightgbm  # noqa: F401
    except (ImportError, OSError) as exc:  # OSError: macOS without libomp
        pytest.skip(f"lightgbm not usable here: {exc.__class__.__name__}")
    from pathlib import Path
    if not Path("models/lightgbm_gdelt.json").exists():
        pytest.skip("served model not present")
    ck = LightGBMCheckpoint("models/lightgbm_gdelt.json")
    if not ck.available:
        pytest.skip(ck.error)
    window = [[np.log1p(100.0)] + [0.0] * 15 for _ in range(14)]
    out = ck.predict(window, 14, "country:CA", "protest", date(2024, 12, 31))
    assert len(out) == 7 and all(p["expected_events"] >= 0 for p in out)  # no extrapolation past 7 days
    assert ck.calibration["log_interval_by_size"]["edges"] == [10.0, 100.0, 1000.0]
    assert ck.metadata["evaluation"]["test"]["strongest_baseline"] == "seasonal_naive"
