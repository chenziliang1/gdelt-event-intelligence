#!/usr/bin/env python3
"""
Choose the interval method on validation, then evaluate the retrained models once on test.

The retrain (2024 plus January 2025, validation February-March 2025, test 2025-04-01..06-11)
uses the cache from build_extended_dataset.py. Two stages, run in this order:

  select  validation only. Fit on the first half of the validation days, score on the second
          half. Candidates: one static size-binned interval (served until now) and rolling
          size-binned intervals refitted at each forecast day on the windows whose targets were
          observed in the previous K days. Picks the method whose worst size bin is closest to
          80% coverage (ties: narrower). Writes the choice; test is not touched.
  test    reads that choice and evaluates every checkpoint, plus the 2024-only served model as
          a reference, on the test windows. Run once.

    python db_scripts/evaluate_retrain.py --stage select --dataset-cache CACHE --checkpoints a.pt b.pt c.pt \
        --out docs/forecast_eval/retrain_2025h1_select.json
    python db_scripts/evaluate_retrain.py --stage test --dataset-cache CACHE --checkpoints a.pt b.pt c.pt \
        --reference models/thp_gdelt.pt --choice docs/forecast_eval/retrain_2025h1_select.json \
        --out docs/forecast_eval/retrain_2025h1_test.json
    python db_scripts/evaluate_retrain.py --stage serve --dataset-cache CACHE --checkpoints - \
        --choice docs/forecast_eval/retrain_2025h1_select.json --tested docs/forecast_eval/retrain_2025h1_test.json \
        --out models/thp_gdelt.pt
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp_eval_utils import (  # noqa: E402
    compare_to_baselines,
    coverage_by_scale,
    fit_log_interval_by_size,
    leak_free_split,
    log_interval_bounds_by_size,
    per_series_win_rate,
)
from thp_inference import Checkpoint  # noqa: E402

HORIZON = 7
ROLLING_DAYS = (14, 28, 42)
NOMINAL = 0.80


def load(cache, val_start_date, test_start_date):
    d = np.load(cache, allow_pickle=False)
    day0 = date.fromisoformat(str(d["first_day"]))
    val_start = (date.fromisoformat(val_start_date) - day0).days
    test_start = (date.fromisoformat(test_start_date) - day0).days
    split = leak_free_split(d["target_positions"], int(d["total_days"]), HORIZON, 0.15, 0.15, val_start, test_start)
    labels = [tuple(x.split("||", 1)) for x in d["labels"].tolist()]
    scale = np.expm1(d["x"][:, :, 0]).mean(axis=1)  # mean daily events over the input window
    return d, split, labels, scale


def static_bounds(pred, y, scale, fit_idx, apply_idx):
    fitted = fit_log_interval_by_size(y[fit_idx], pred[fit_idx], scale[fit_idx])
    return log_interval_bounds_by_size(pred[apply_idx], scale[apply_idx], fitted)


def rolling_bounds(pred, y, scale, pos, pool_idx, apply_idx, days):
    """At each forecast day t, refit on pool windows whose last target day is in [t - days, t - 1]."""
    lo = np.empty((len(apply_idx), HORIZON))
    hi = np.empty_like(lo)
    last = pos[pool_idx] + HORIZON - 1
    for t in np.unique(pos[apply_idx]):
        fit = pool_idx[(last < t) & (last >= t - days)]
        rows = np.where(pos[apply_idx] == t)[0]
        fitted = fit_log_interval_by_size(y[fit], pred[fit], scale[fit])
        lo[rows], hi[rows] = log_interval_bounds_by_size(pred[apply_idx][rows], scale[apply_idx][rows], fitted)
    return lo, hi


def coverage(y, lo, hi, scale):
    inside = (y >= lo) & (y <= hi)
    by_size = coverage_by_scale(y, inside, np.repeat(scale[:, None], HORIZON, axis=1))
    return {"overall": round(float(inside.mean()), 3), "by_size": by_size,
            "worst_bin_gap": round(max(abs(v - NOMINAL) for v in by_size.values()), 3),
            "mean_width": round(float((hi - lo).mean()), 1)}


def methods(pred, y, scale, pos, fit_idx, pool_idx, apply_idx):
    out = {"static": static_bounds(pred, y, scale, fit_idx, apply_idx)}
    for days in ROLLING_DAYS:
        out[f"rolling_{days}d"] = rolling_bounds(pred, y, scale, pos, pool_idx, apply_idx, days)
    return out


def predict_all(path, d, labels, idx):
    ck = Checkpoint(path)
    pred = np.zeros_like(d["y_count"], dtype=np.float64)
    pred[idx] = ck.predict(d["x"][idx], [labels[i] for i in idx], HORIZON)
    return pred


def select(args) -> dict:
    d, split, labels, scale = load(args.dataset_cache, args.val_start_date, args.test_start_date)
    pos, y = d["target_positions"], d["y_count"].astype(np.float64)
    val = split.val_idx
    mid = split.val_start + (split.test_start - split.val_start) // 2
    fit_idx = val[pos[val] + HORIZON - 1 < mid]  # first half: targets observed before the second half starts
    eval_idx = val[pos[val] >= mid]
    out = {"stage": "select", "val_days": [args.val_start_date, args.test_start_date],
           "fit_windows": int(len(fit_idx)), "eval_windows": int(len(eval_idx)), "checkpoints": {}}
    scores = {}
    for path in args.checkpoints:
        pred = predict_all(path, d, labels, val)
        res = {name: coverage(y[eval_idx], lo, hi, scale[eval_idx])
               for name, (lo, hi) in methods(pred, y, scale, pos, fit_idx, val, eval_idx).items()}
        cmp = compare_to_baselines(y[val], pred[val], {"seasonal_naive": d["baseline_seasonal_naive"][val]})
        out["checkpoints"][path] = {"validation_mae": cmp["model_mae"], "validation_seasonal_naive": cmp["baseline_mae"]["seasonal_naive"],
                                    "intervals_on_second_half": res}
        for name, r in res.items():
            scores.setdefault(name, []).append((r["worst_bin_gap"], r["mean_width"]))
        print(f"{Path(path).name}: val MAE {cmp['model_mae']} (seasonal-naive {cmp['baseline_mae']['seasonal_naive']})")
        for name, r in res.items():
            print(f"   {name:12s} overall {r['overall']} by size {r['by_size']} worst gap {r['worst_bin_gap']} width {r['mean_width']}")
    mean_scores = {k: (round(float(np.mean([a for a, _ in v])), 3), round(float(np.mean([b for _, b in v])), 1)) for k, v in scores.items()}
    out["mean_worst_bin_gap_and_width"] = mean_scores
    out["chosen_interval_method"] = min(mean_scores, key=lambda k: mean_scores[k])
    out["served_checkpoint"] = min(args.checkpoints, key=lambda p: out["checkpoints"][p]["validation_mae"])
    print("chosen interval method:", out["chosen_interval_method"], "| served checkpoint (best validation MAE):", out["served_checkpoint"])
    return out


def test(args) -> dict:
    choice = json.loads(Path(args.choice).read_text())
    method = choice["chosen_interval_method"]
    d, split, labels, scale = load(args.dataset_cache, args.val_start_date, args.test_start_date)
    pos, y = d["target_positions"], d["y_count"].astype(np.float64)
    val, tst = split.val_idx, split.test_idx
    pool = np.concatenate([val, tst])
    naive = d["baseline_seasonal_naive"]
    last_day = date.fromisoformat(str(d["first_day"])) + timedelta(days=int(d["total_days"]) - 1)
    out = {"stage": "test", "test_days": [args.test_start_date, last_day.isoformat()],
           "windows": int(len(tst)), "interval_method": method, "seasonal_naive_mae": None, "checkpoints": {}}
    for path in [*args.checkpoints, *([args.reference] if args.reference else [])]:
        pred = predict_all(path, d, labels, pool)
        cmp = compare_to_baselines(y[tst], pred[tst], {"seasonal_naive": naive[tst]})
        cmp["per_series_vs_seasonal_naive"] = per_series_win_rate(
            np.asarray([f"{a}||{b}" for a, b in labels])[tst], y[tst], pred[tst], naive[tst])
        bounds = methods(pred, y, scale, pos, val, pool, tst)
        cmp["intervals"] = {name: coverage(y[tst], lo, hi, scale[tst]) for name, (lo, hi) in bounds.items()}
        cmp["role"] = "reference: 2024-only served model" if path == args.reference else "retrained"
        out["checkpoints"][path] = cmp
        out["seasonal_naive_mae"] = cmp["baseline_mae"]["seasonal_naive"]
        r = cmp["intervals"][method]
        print(f"{Path(path).name}: test MAE {cmp['model_mae']} seasonal-naive {out['seasonal_naive_mae']} "
              f"improvement {cmp['improvement_pct_vs_strongest']}% series won {cmp['per_series_vs_seasonal_naive']['win_rate']} "
              f"| {method} coverage {r['overall']} {r['by_size']} width {r['mean_width']} | static {cmp['intervals']['static']['overall']} {cmp['intervals']['static']['by_size']}")
    return out


def serve(args) -> dict:
    """Write the served checkpoint: the one chosen on validation, its intervals in the chosen method's
    state at the end of the data (rolling: refitted on the last K days of observed windows), and the
    test result in metadata.evaluation.test, which the API reports as baseline_comparison."""
    import torch

    choice = json.loads(Path(args.choice).read_text())
    tested = json.loads(Path(args.tested).read_text())
    path, method = choice["served_checkpoint"], choice["chosen_interval_method"]
    d, split, labels, scale = load(args.dataset_cache, args.val_start_date, args.test_start_date)
    pos, y = d["target_positions"], d["y_count"].astype(np.float64)
    pool = np.concatenate([split.val_idx, split.test_idx])
    pred = predict_all(path, d, labels, pool)
    last = pos[pool] + HORIZON - 1
    days = int(method.split("_")[1][:-1]) if method.startswith("rolling") else None
    fit = pool[last >= last.max() - days + 1] if days else split.val_idx
    fitted = fit_log_interval_by_size(y[fit], pred[fit], scale[fit])

    raw = torch.load(path, map_location="cpu", weights_only=False)
    raw["calibration"]["log_interval_by_size"] = fitted
    raw["calibration"]["log_interval_by_size_report"] = {
        "method": method, "fitted_on_windows": int(len(fit)),
        "test": tested["checkpoints"][path]["intervals"][method]}
    t = dict(tested["checkpoints"][path])
    t["interval_coverage_80"] = t.pop("intervals")[method]
    t["period"] = tested["test_days"]
    raw["metadata"]["evaluation"]["test"] = t
    raw["metadata"]["training_period"] = "2024-01-01 to 2025-01-31 (validation 2025-02-01 to 2025-03-31)"
    torch.save(raw, args.out)
    print(f"served {path} -> {args.out}: test MAE {t['model_mae']} (+{t['improvement_pct_vs_strongest']}% vs "
          f"{t['strongest_baseline']}), intervals {method} fitted on {len(fit)} windows")
    return {}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("select", "test", "serve"), required=True)
    p.add_argument("--dataset-cache", required=True)
    p.add_argument("--checkpoints", nargs="+", required=True)
    p.add_argument("--val-start-date", default="2025-02-01")
    p.add_argument("--test-start-date", default="2025-04-01")
    p.add_argument("--reference")
    p.add_argument("--choice")
    p.add_argument("--tested", help="serve: the test-stage JSON")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    if args.stage == "serve":
        serve(args)
        return 0
    out = select(args) if args.stage == "select" else test(args)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
