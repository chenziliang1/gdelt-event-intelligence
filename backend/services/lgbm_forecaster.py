"""
The served forecaster since 2026-10-09: gradient boosting (LightGBM) on the Transformer's inputs.

Chosen by a rule fixed before the test period was loaded (docs/forecast_eval/2025q3_decision_rule.md):
on 2025 Q3, unseen by both, it was 13.3% below seasonal-naive against 4.5% for the Transformer, with a
bootstrap interval for the gap clear of zero. The served model is exactly the tested one (trained on
2024 plus January 2025, seed 2025), written by `db_scripts/evaluate_strong_baseline.py --stage serve`.

Same interface as thp_neural.NeuralTHPCheckpoint (available, error, metadata, calibration, seq_len,
predict), so thp_service serves either; FORECAST_MODEL_PATH picks the file (a .pt is the Transformer).
It predicts 7 days: horizons beyond the training horizon are not extrapolated.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

HORIZON = 7
SEQ_LEN = 14


def lgbm_feature_rows(x: np.ndarray, kind_id: int, measure_id: int, first_target_dow: int) -> np.ndarray:
    """The model's input rows for one window, one per horizon day; exactly the columns of
    db_scripts/evaluate_strong_baseline.features (without series id): the 14 daily log counts, the
    last input day's other 15 features, the same-weekday log count a week before the target day,
    the horizon, the target's day of week, the series kind and the measure."""
    x = np.asarray(x, dtype=np.float32)
    hist, last = x[:, 0], x[-1, 1:]
    rows = []
    for h in range(HORIZON):
        rows.append(np.concatenate([hist, last, [hist[7 + h], h, (first_target_dow + h) % 7, kind_id, measure_id]]))
    return np.asarray(rows, dtype=np.float32)


class LightGBMCheckpoint:
    """Lazy loader and inference wrapper for the served LightGBM bundle (JSON)."""

    model_name = "lightgbm_seasonal_residual_v1"

    def __init__(self, path: str | Path):
        self.checkpoint_path = Path(path)
        self._loaded = False
        self._available = False
        self._error: Optional[str] = None
        self._booster = None
        self._bundle: Dict[str, Any] = {}

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._available

    @property
    def error(self) -> Optional[str]:
        self._ensure_loaded()
        return self._error

    @property
    def metadata(self) -> Dict[str, Any]:
        self._ensure_loaded()
        return self._bundle.get("metadata", {})

    @property
    def calibration(self) -> Dict[str, Any]:
        self._ensure_loaded()
        return self._bundle.get("calibration", {})

    @property
    def seq_len(self) -> int:
        return SEQ_LEN

    def predict(
        self,
        feature_window: List[List[float]],
        forecast_days: int,
        series_key: Optional[str] = None,
        event_type: str = "conflict",
        last_date: Optional[date | datetime] = None,
    ) -> Optional[List[Dict[str, float]]]:
        self._ensure_loaded()
        if not self._available or len(feature_window) < SEQ_LEN or last_date is None:
            return None
        if isinstance(last_date, datetime):
            last_date = last_date.date()
        kinds, measures = self._bundle["kinds"], self._bundle["measures"]
        kind = (series_key or "global:ALL").split(":", 1)[0]
        measure = (event_type or "all").lower()
        if kind not in kinds or measure not in measures:
            return None
        x = np.asarray(feature_window[-SEQ_LEN:], dtype=np.float32)
        first_dow = (last_date + timedelta(days=1)).weekday()
        rows = lgbm_feature_rows(x, kinds[kind], measures[measure], first_dow)
        residual = self._booster.predict(rows, num_iteration=self._bundle["best_iteration"])
        expected = np.maximum(np.expm1(residual + rows[:, SEQ_LEN + 15]), 0.0)  # + seasonal_log
        days = max(1, min(int(forecast_days), HORIZON))
        return [{"horizon": float(h + 1), "expected_events": float(expected[h])} for h in range(days)]

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.checkpoint_path.exists():
            self._error = f"model not found: {self.checkpoint_path}"
            return
        try:
            import lightgbm as lgb
        except Exception as exc:  # noqa: BLE001
            self._error = f"lightgbm import failed: {exc}"
            return
        try:
            self._bundle = json.loads(self.checkpoint_path.read_text())
            self._booster = lgb.Booster(model_str=self._bundle["booster"])
            self._available = True
        except Exception as exc:  # noqa: BLE001
            self._error = f"model load failed: {exc}"
