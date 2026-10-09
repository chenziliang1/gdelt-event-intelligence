"""
Evaluation helpers for the THP forecaster. numpy only (no torch, no database),
so they are unit tested and usable from the training script and from the
baseline CLI alike.

What these fix, relative to the original evaluation:

* Window leakage. Windows used to be assigned to train or validation by their
  *first forecast day*, so a training window whose first target was just before
  the cut still had targets extending 6 days into validation. ``leak_free_split``
  requires the whole forecast span to sit inside one partition.
* No held-out test set. Validation was used for early stopping, model
  selection and calibration, then reported as the result. The split now has a
  separate test partition that is evaluated once.
* Weak baseline. The reference was a flat 7-day average. ``seasonal_naive``
  (same weekday last week) is the baseline a daily count series with a weekly
  cycle has to beat, and it is the strongest of the baselines tried.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def seasonal_naive(history_counts: np.ndarray, horizon: int, period: int = 7) -> np.ndarray:
    """Forecast each future day with the count from ``period`` days earlier.

    ``history_counts`` is ``(n, seq_len)``; the last column is the day before
    the first forecast day. Day ``h`` (0-based) of the forecast maps to column
    ``-period + (h % period)``.
    """
    history_counts = np.asarray(history_counts, dtype=np.float64)
    if history_counts.shape[1] < period:
        raise ValueError(f"need at least {period} days of history, got {history_counts.shape[1]}")
    last_cycle = history_counts[:, -period:]
    cols = [h % period for h in range(horizon)]
    return np.maximum(last_cycle[:, cols], 0.0).astype(np.float32)


def seasonal_naive_from_log_features(x: np.ndarray, horizon: int, period: int = 7) -> np.ndarray:
    """Same baseline computed from the training feature tensor.

    Feature 0 of every day is ``log1p(count)`` (see ``make_dataset``), so
    cached datasets built before this baseline existed do not need rebuilding.
    """
    counts = np.expm1(np.asarray(x)[:, :, 0])
    return seasonal_naive(counts, horizon, period)


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Split:
    train_idx: np.ndarray
    val_idx: np.ndarray
    test_idx: np.ndarray
    val_start: int
    test_start: int
    dropped_boundary_windows: int

    def sizes(self) -> Dict[str, int]:
        return {
            "train": int(len(self.train_idx)),
            "val": int(len(self.val_idx)),
            "test": int(len(self.test_idx)),
            "dropped_boundary_windows": int(self.dropped_boundary_windows),
        }


def leak_free_split(
    target_positions: np.ndarray,
    total_days: int,
    horizon: int,
    val_fraction: float = 0.15,
    test_fraction: float = 0.15,
    val_start_day: Optional[int] = None,
    test_start_day: Optional[int] = None,
) -> Split:
    """Chronological train / validation / test split with no forecast overlap.

    ``target_positions[i]`` is the day index of window ``i``'s first forecast
    day, so its forecast spans ``[p, p + horizon - 1]``. A window belongs to a
    partition only if that *entire* span lies inside it. Windows whose span
    straddles a boundary are dropped, not assigned to either side.

    Inputs (history) may reach back into earlier partitions; that is ordinary
    forecasting, not leakage. Targets must not.

    ``val_start_day`` / ``test_start_day`` (day indices) replace the fractions, so a partition can
    be a calendar period (validation = late Q1 2025, test = Q2 2025).
    """
    val_fraction = min(max(float(val_fraction), 0.05), 0.4)
    test_fraction = min(max(float(test_fraction), 0.0), 0.4)
    test_start = int(total_days * (1.0 - test_fraction)) if test_fraction > 0 else total_days
    val_start = int(total_days * (1.0 - test_fraction - val_fraction))
    if test_start_day is not None:
        test_start = int(test_start_day)
    if val_start_day is not None:
        val_start = int(val_start_day)

    pos = np.asarray(target_positions)
    last_target = pos + horizon - 1

    train = np.where(last_target < val_start)[0]
    val = np.where((pos >= val_start) & (last_target < test_start))[0]
    test = np.where(pos >= test_start)[0] if test_fraction > 0 else np.array([], dtype=np.int64)

    if len(train) == 0 or len(val) == 0:
        raise RuntimeError(
            "Split produced an empty train or validation set. "
            "Use smaller validation/test fractions or a shorter sequence length."
        )
    kept = len(train) + len(val) + len(test)
    return Split(train, val, test, val_start, test_start, int(len(pos) - kept))


def max_train_target_day(split: Split, target_positions: np.ndarray, horizon: int) -> int:
    """Last forecast day used in training. Must be < ``split.val_start``."""
    return int((np.asarray(target_positions)[split.train_idx] + horizon - 1).max())


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(y_true, dtype=np.float64) - np.asarray(y_pred, dtype=np.float64))))


def compare_to_baselines(
    y_true: np.ndarray,
    model_pred: Optional[np.ndarray],
    baselines: Mapping[str, np.ndarray],
) -> Dict[str, object]:
    """MAE for each baseline (and the model, if given) with improvement
    measured against the *strongest* baseline as well as each one.

    The headline improvement should be reported against ``strongest_baseline``;
    reporting only against a weak one is how a ~19% gain looked like 50%.
    """
    baseline_mae = {name: mae(y_true, pred) for name, pred in baselines.items()}
    strongest = min(baseline_mae, key=baseline_mae.get)
    out: Dict[str, object] = {
        "baseline_mae": {k: round(v, 2) for k, v in baseline_mae.items()},
        "strongest_baseline": strongest,
    }
    if model_pred is not None:
        model_mae = mae(y_true, model_pred)
        out["model_mae"] = round(model_mae, 2)
        out["improvement_pct_vs"] = {
            name: round((b - model_mae) / b * 100.0, 1) if b > 0 else None
            for name, b in baseline_mae.items()
        }
        out["improvement_pct_vs_strongest"] = out["improvement_pct_vs"][strongest]
    return out


def per_series_win_rate(
    labels: Sequence[str],
    y_true: np.ndarray,
    model_pred: np.ndarray,
    baseline_pred: np.ndarray,
) -> Dict[str, float]:
    """Share of series where the model's MAE beats the baseline's.

    Aggregate MAE is dominated by the few largest series (736 series, very
    different scales). A model that wins on the big ones but loses on most
    series should not be described as a uniform improvement.
    """
    labels = np.asarray(labels)
    wins, total = 0, 0
    for label in np.unique(labels):
        m = labels == label
        total += 1
        if mae(y_true[m], model_pred[m]) < mae(y_true[m], baseline_pred[m]):
            wins += 1
    return {"series": total, "model_wins": wins, "win_rate": round(wins / total, 3) if total else 0.0}


def fit_horizon_scale(y_true: np.ndarray, y_pred: np.ndarray) -> list:
    """Per-horizon multiplicative correction that minimises MAE, fitted on VALIDATION.

    For ``y ~ s * pred`` the MAE-optimal ``s`` is the median of ``y / pred`` weighted by
    ``pred``. Fit on validation, apply unchanged to test.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    scales = []
    for h in range(y_true.shape[1]):
        p, y = y_pred[:, h], y_true[:, h]
        keep = p > 1e-6
        ratio, weight = y[keep] / p[keep], p[keep]
        order = np.argsort(ratio)
        cum = np.cumsum(weight[order])
        idx = int(np.searchsorted(cum, cum[-1] / 2.0)) if len(cum) else 0
        scales.append(float(ratio[order][idx]) if len(cum) else 1.0)
    return scales


def apply_horizon_scale(y_pred: np.ndarray, scales: Sequence[float]) -> np.ndarray:
    return (np.asarray(y_pred, dtype=np.float64) * np.asarray(scales)[None, :]).astype(np.float32)


def fit_log_interval(y_true: np.ndarray, y_pred: np.ndarray, lo: float = 0.10, hi: float = 0.90) -> list:
    """Per-horizon quantiles of ``log1p(y) - log1p(pred)`` on VALIDATION.

    The original interval added the same count-space residual to every series, so a
    series averaging 5 events got the same +-width as one averaging 5,000: too wide for
    small series, too narrow for large ones (test coverage 0.75 against a nominal 0.80).
    A log-space residual scales with the series.
    """
    r = np.log1p(np.asarray(y_true, dtype=np.float64)) - np.log1p(np.asarray(y_pred, dtype=np.float64))
    return [
        {"horizon": h + 1, "log_residual_q_lo": float(np.quantile(r[:, h], lo)),
         "log_residual_q_hi": float(np.quantile(r[:, h], hi)), "nominal": hi - lo}
        for h in range(r.shape[1])
    ]


def log_interval_bounds(y_pred: np.ndarray, horizons: Sequence[Mapping[str, float]]) -> tuple:
    base = np.log1p(np.asarray(y_pred, dtype=np.float64))
    lo = np.asarray([q["log_residual_q_lo"] for q in horizons])[None, :]
    hi = np.asarray([q["log_residual_q_hi"] for q in horizons])[None, :]
    return np.maximum(np.expm1(base + lo), 0.0), np.expm1(base + hi)


def log_interval_coverage(
    y_true: np.ndarray, y_pred: np.ndarray, horizons: Sequence[Mapping[str, float]]
) -> Dict[str, object]:
    """Coverage of the log-space interval. Call on TEST with quantiles fitted on validation."""
    y_true = np.asarray(y_true, dtype=np.float64)
    low, high = log_interval_bounds(y_pred, horizons)
    inside = (y_true >= low) & (y_true <= high)
    per_horizon = inside.mean(axis=0)
    return {
        "nominal": round(float(horizons[0]["nominal"]), 2) if horizons else None,
        "overall": round(float(per_horizon.mean()), 3),
        "per_horizon": [round(float(v), 3) for v in per_horizon],
        "mean_width": round(float(np.mean(high - low)), 2),
    }


SIZE_EDGES = (10.0, 100.0, 1000.0)  # mean daily events over the input window


def size_bin(scale: np.ndarray, edges: Sequence[float] = SIZE_EDGES) -> np.ndarray:
    return np.searchsorted(np.asarray(edges), np.asarray(scale, dtype=np.float64), side="right")


def fit_log_interval_by_size(
    y_true: np.ndarray, y_pred: np.ndarray, scale: np.ndarray,
    edges: Sequence[float] = SIZE_EDGES, lo: float = 0.10, hi: float = 0.90, min_rows: int = 200,
) -> Dict[str, object]:
    """Log-space quantiles per series-size bin and horizon, fitted on VALIDATION.

    One pooled set of quantiles over-covered small series and under-covered 10-100 event series
    (0.78 / 0.55 / 0.77 / 0.87 by size). A bin with fewer than ``min_rows`` windows falls back
    to the pooled quantiles.
    """
    pooled = fit_log_interval(y_true, y_pred, lo, hi)
    bins = size_bin(scale, edges)
    per_bin = {}
    for b in range(len(edges) + 1):
        m = bins == b
        per_bin[str(b)] = fit_log_interval(y_true[m], y_pred[m], lo, hi) if m.sum() >= min_rows else pooled
    return {"edges": list(edges), "bins": per_bin, "pooled": pooled}


def log_interval_bounds_by_size(y_pred: np.ndarray, scale: np.ndarray, fitted: Mapping[str, object]) -> tuple:
    bins = size_bin(scale, fitted["edges"])
    low = np.empty_like(np.asarray(y_pred, dtype=np.float64))
    high = np.empty_like(low)
    for b, quantiles in fitted["bins"].items():
        m = bins == int(b)
        if m.any():
            low[m], high[m] = log_interval_bounds(np.asarray(y_pred)[m], quantiles)
    return low, high


def coverage_by_scale(
    y_true: np.ndarray, inside: np.ndarray, series_scale: np.ndarray, bins: Sequence[float] = (10, 100, 1000)
) -> Dict[str, float]:
    """Coverage split by series size, where a pooled number can hide opposite errors."""
    edges = [-np.inf, *bins, np.inf]
    out = {}
    for a, b in zip(edges[:-1], edges[1:]):
        m = (series_scale >= a) & (series_scale < b)
        if m.any():
            label = f"{'' if a == -np.inf else int(a)}-{'' if b == np.inf else int(b)}"
            out[label] = round(float(inside[m].mean()), 3)
    return out


def interval_coverage(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    horizons: Sequence[Mapping[str, float]],
) -> Dict[str, object]:
    """Empirical coverage of the central-80% interval built from residual quantiles.

    ``horizons`` is ``residual_calibration(...)["horizons"]``: per-horizon
    ``residual_q10`` / ``residual_q90`` fitted on VALIDATION residuals. Call this
    on the TEST partition: if the interval is honest, coverage should be near
    0.80. Coverage computed on the same data the quantiles were fitted on is
    0.80 by construction and proves nothing.
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    per_horizon = []
    for h, q in enumerate(horizons):
        lo = y_pred[:, h] + float(q["residual_q10"])
        hi = y_pred[:, h] + float(q["residual_q90"])
        per_horizon.append(float(np.mean((y_true[:, h] >= lo) & (y_true[:, h] <= hi))))
    return {
        "nominal": 0.80,
        "overall": round(float(np.mean(per_horizon)), 3),
        "per_horizon": [round(v, 3) for v in per_horizon],
    }


# ---------------------------------------------------------------------------
# Uncertainty of a difference over one test period
# ---------------------------------------------------------------------------

def moving_block_bootstrap_ci(
    per_day: np.ndarray,
    block: int = 7,
    n_resamples: int = 2000,
    seed: int = 0,
    level: float = 0.95,
) -> Dict[str, float]:
    """Percentile interval for the mean of a daily series, resampling blocks of consecutive days.

    ``per_day`` is one value per test start day, in date order (for a comparison: the day's mean
    absolute error of the model minus the baseline's; every day has the same number of windows,
    so the mean over days is the overall MAE difference). Neighbouring days share targets (a
    7-day horizon) and news cycles, so days are not independent; resampling ``block``-day runs
    keeps that dependence. It measures how the result would vary over other days like these,
    not over other periods or other training seeds.
    """
    values = np.asarray(per_day, dtype=np.float64)
    n = len(values)
    if n < block:
        raise ValueError(f"need at least {block} days, got {n}")
    starts = np.arange(n - block + 1)
    k = -(-n // block)  # blocks per resample, ceil(n / block)
    rng = np.random.default_rng(seed)
    picks = rng.choice(starts, size=(n_resamples, k))
    idx = (picks[:, :, None] + np.arange(block)).reshape(n_resamples, -1)[:, :n]
    means = values[idx].mean(axis=1)
    alpha = (1.0 - level) / 2.0
    return {
        "mean": float(values.mean()),
        "lo": float(np.quantile(means, alpha)),
        "hi": float(np.quantile(means, 1.0 - alpha)),
        "level": level,
        "block_days": block,
        "resamples": n_resamples,
        "days": n,
    }
