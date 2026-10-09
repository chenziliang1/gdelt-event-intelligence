#!/usr/bin/env python3
"""
A gradient-boosting baseline for the 2025 retrain, and bootstrap intervals for every comparison.

Same cache, split and windows as evaluate_retrain.py (train 2024 + January 2025, validation
February-March 2025, test 2025-04-01..06-11, 48,576 test windows), same count-space MAE.

  transformer  torch only. Re-predicts the test windows with each retrained checkpoint and saves
               the per-window predictions (they reproduce retrain_2025h1_test.json).
  select       LightGBM only, validation only. A few configurations (target, loss, series id,
               leaves), early stopping on validation, chosen by validation count MAE.
  test         trains the chosen configuration once more and predicts the test windows. Run once.
  report       no model: MAE, per-series win rate against seasonal-naive, and moving-block
               bootstrap intervals (7-day blocks over test start days, 2,000 resamples).

torch and LightGBM are kept in separate processes: both link an OpenMP runtime. On macOS
LightGBM needs libomp (`brew install libomp`, or point DYLD_FALLBACK_LIBRARY_PATH at torch/lib).

    CACHE=models/thp_dataset_2024_2025h1_seq14_h7.npz
    python db_scripts/evaluate_strong_baseline.py --stage transformer --dataset-cache $CACHE \
        --checkpoints models/retrain_2025h1/seed42.pt models/retrain_2025h1/seed1.pt models/retrain_2025h1/seed2.pt
    python db_scripts/evaluate_strong_baseline.py --stage select --dataset-cache $CACHE --out docs/forecast_eval/strong_baseline_select.json
    python db_scripts/evaluate_strong_baseline.py --stage test --dataset-cache $CACHE --choice docs/forecast_eval/strong_baseline_select.json
    python db_scripts/evaluate_strong_baseline.py --stage report --dataset-cache $CACHE --out docs/forecast_eval/strong_baseline_test.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp_eval_utils import compare_to_baselines, mae, moving_block_bootstrap_ci, per_series_win_rate  # noqa: E402

HORIZON = 7
SEED = 2025
WORK = Path("models/strong_baseline")  # per-window predictions, not committed

# Validation trials. The first four settle target and loss; the last three vary one thing
# from the best of those (filled in at run time).
BASE = {"target": "residual", "objective": "l1", "series_id": True, "num_leaves": 63}
TRIALS = [
    {"target": "residual", "objective": "l1"},
    {"target": "residual", "objective": "l2"},
    {"target": "log", "objective": "l1"},
    {"target": "log", "objective": "l2"},
]
VARIANTS = [{"series_id": False}, {"num_leaves": 31}, {"num_leaves": 127}]


def load(cache, val_start_date="2025-02-01", test_start_date="2025-04-01"):
    from thp_eval_utils import leak_free_split
    d = np.load(cache, allow_pickle=False)
    day0 = date.fromisoformat(str(d["first_day"]))
    split = leak_free_split(d["target_positions"], int(d["total_days"]), HORIZON, 0.15, 0.15,
                            (date.fromisoformat(val_start_date) - day0).days,
                            (date.fromisoformat(test_start_date) - day0).days)
    return d, split, day0


def features(d, idx, day0, series_id: bool):
    """One row per (window, horizon day). Columns: the 14 daily log counts, the last input day's
    other 15 features, the same-weekday log count a week before the target day, the horizon,
    the target's day of week, and the series' kind and measure as categories (and its id)."""
    x = d["x"][idx]
    hist = x[:, :, 0]
    last = x[:, -1, 1:]
    n, h = len(idx), np.arange(HORIZON)
    all_labels = np.unique(d["labels"])
    labels = np.asarray(d["labels"])[idx]
    # A label is "<kind>:<name>||<measure>", e.g. "actor:POLICE||protest" (7 kinds x 4 measures).
    kinds = {k: i for i, k in enumerate(sorted({s.split(":", 1)[0] for s in all_labels}))}
    measures = {m: i for i, m in enumerate(sorted({s.split("||", 1)[1] for s in all_labels}))}
    cats_s = {s: i for i, s in enumerate(all_labels)}
    dow0 = np.asarray([(day0 + timedelta(days=int(p))).weekday() for p in d["target_positions"][idx]])
    rep = lambda a: np.repeat(a, HORIZON, axis=0)  # noqa: E731
    hh = np.tile(h, n)
    seasonal = hist[np.repeat(np.arange(n), HORIZON), 7 + hh]  # same weekday, one week before the target
    cols = [rep(hist), rep(last), seasonal[:, None], hh[:, None], ((rep(dow0) + hh) % 7)[:, None],
            rep(np.asarray([kinds[s.split(":", 1)[0]] for s in labels]))[:, None],
            rep(np.asarray([measures[s.split("||", 1)[1]] for s in labels]))[:, None]]
    names = [f"log_count_d{i}" for i in range(14)] + [f"last_f{i}" for i in range(1, 16)] + \
            ["seasonal_log", "horizon", "target_dow", "series_kind", "series_measure"]
    categorical = ["target_dow", "series_kind", "series_measure"]
    if series_id:
        cols.append(rep(np.asarray([cats_s[s] for s in labels]))[:, None])
        names.append("series_id")
        categorical.append("series_id")
    return np.hstack(cols).astype(np.float32), names, categorical, seasonal


def to_target(d, idx, seasonal_log, target):
    y_log = np.log1p(d["y_count"][idx].astype(np.float64)).reshape(-1)
    return y_log - seasonal_log if target == "residual" else y_log


def to_counts(pred, seasonal_log, target, n):
    log = pred + seasonal_log if target == "residual" else pred
    return np.maximum(np.expm1(log), 0.0).reshape(n, HORIZON)


def fit(cfg, d, split, day0):
    import lightgbm as lgb
    tr, va = split.train_idx, split.val_idx
    Xtr, names, cat, s_tr = features(d, tr, day0, cfg["series_id"])
    Xva, _, _, s_va = features(d, va, day0, cfg["series_id"])
    params = {"objective": cfg["objective"], "learning_rate": 0.05, "num_leaves": cfg["num_leaves"],
              "min_data_in_leaf": 100, "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1,
              "lambda_l2": 1.0, "seed": SEED, "deterministic": True, "num_threads": 8, "verbose": -1,
              "metric": "l1"}
    dtr = lgb.Dataset(Xtr, to_target(d, tr, s_tr, cfg["target"]), feature_name=names, categorical_feature=cat)
    dva = lgb.Dataset(Xva, to_target(d, va, s_va, cfg["target"]), reference=dtr)
    booster = lgb.train(params, dtr, num_boost_round=3000, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(100, verbose=False)])
    return booster


def predict(booster, cfg, d, idx, day0):
    X, _, _, s = features(d, idx, day0, cfg["series_id"])
    return to_counts(booster.predict(X, num_iteration=booster.best_iteration), s, cfg["target"], len(idx))


def stage_transformer(args):
    from evaluate_retrain import predict_all
    d, split, _ = load(args.dataset_cache)
    labels = [tuple(x.split("||", 1)) for x in d["labels"].tolist()]
    tst = split.test_idx
    preds = {}
    for path in args.checkpoints:
        p = predict_all(path, d, labels, tst)[tst]
        preds[Path(path).stem] = p.astype(np.float32)
        print(f"{path}: test MAE {mae(d['y_count'][tst], p):.2f}")
    WORK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(WORK / "transformer_test_preds.npz", **preds)


def stage_select(args):
    d, split, day0 = load(args.dataset_cache)
    va, y = split.val_idx, d["y_count"].astype(np.float64)
    naive = d["baseline_seasonal_naive"][va]
    out = {"stage": "select", "train_windows": int(len(split.train_idx)), "val_windows": int(len(va)),
           "validation_seasonal_naive_mae": round(mae(y[va], naive), 2), "trials": []}

    def run(cfg):
        t0 = time.time()
        booster = fit(cfg, d, split, day0)
        val_mae = mae(y[va], predict(booster, cfg, d, va, day0))
        row = {**cfg, "best_iteration": int(booster.best_iteration), "validation_mae": round(val_mae, 2),
               "seconds": round(time.time() - t0)}
        out["trials"].append(row)
        print(row, flush=True)
        return val_mae

    first = {i: run({**BASE, **t}) for i, t in enumerate(TRIALS)}
    best = {**BASE, **TRIALS[min(first, key=first.get)]}
    for v in VARIANTS:
        run({**best, **v})
    chosen = min(out["trials"], key=lambda r: r["validation_mae"])
    out["chosen"] = {k: chosen[k] for k in ("target", "objective", "series_id", "num_leaves")}
    print("chosen:", out["chosen"], chosen["validation_mae"])
    write(args.out, out)


def stage_test(args):
    cfg = json.loads(Path(args.choice).read_text())["chosen"]
    d, split, day0 = load(args.dataset_cache)
    booster = fit(cfg, d, split, day0)
    p = predict(booster, cfg, d, split.test_idx, day0)
    WORK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(WORK / "lightgbm_test_preds.npz", lightgbm=p.astype(np.float32))
    print(f"LightGBM {cfg}: best iteration {booster.best_iteration}, test MAE "
          f"{mae(d['y_count'][split.test_idx], p):.2f}")


def stage_report(args):
    d, split, day0 = load(args.dataset_cache)
    tst = split.test_idx
    y = d["y_count"][tst].astype(np.float64)
    naive = d["baseline_seasonal_naive"][tst].astype(np.float64)
    tr = dict(np.load(WORK / "transformer_test_preds.npz"))
    lgbm = np.load(WORK / "lightgbm_test_preds.npz")["lightgbm"].astype(np.float64)
    labels = np.asarray(d["labels"])[tst]
    pos = d["target_positions"][tst]
    days = np.unique(pos)

    def per_day(pred):
        err = np.abs(y - pred).mean(axis=1)
        return np.asarray([err[pos == t].mean() for t in days])

    def block(name, pred):
        cmp = compare_to_baselines(y, pred, {"seasonal_naive": naive})
        return {"test_mae": cmp["model_mae"], "improvement_pct_vs_seasonal_naive": cmp["improvement_pct_vs_strongest"],
                "per_series_vs_seasonal_naive": per_series_win_rate(labels, y, pred, naive)}

    models = {f"transformer_{k}": v.astype(np.float64) for k, v in tr.items()}
    models["lightgbm"] = lgbm
    res = {name: block(name, p) for name, p in models.items()}
    seeds = [k for k in models if k.startswith("transformer_")]
    day = {name: per_day(p) for name, p in models.items()}
    day["seasonal_naive"] = per_day(naive)
    day["transformer_seed_mean"] = np.mean([day[k] for k in seeds], axis=0)  # mean of the seeds' MAEs

    def ci(a, b):
        r = moving_block_bootstrap_ci(day[a] - day[b], block=7, n_resamples=2000, seed=SEED)
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}

    comparisons = {
        "transformer_seed_mean_minus_seasonal_naive": ci("transformer_seed_mean", "seasonal_naive"),
        "lightgbm_minus_seasonal_naive": ci("lightgbm", "seasonal_naive"),
        "transformer_seed_mean_minus_lightgbm": ci("transformer_seed_mean", "lightgbm"),
        **{f"{k}_minus_seasonal_naive": ci(k, "seasonal_naive") for k in seeds},
        **{f"{k}_minus_lightgbm": ci(k, "lightgbm") for k in seeds},
    }
    out = {"stage": "report", "test_days": [str(day0 + timedelta(days=int(days[0]))),
                                            str(day0 + timedelta(days=int(days[-1]) + HORIZON - 1))],
           "test_windows": int(len(tst)), "start_days": int(len(days)),
           "seasonal_naive_mae": round(mae(y, naive), 2),
           "transformer_seed_mean_mae": round(float(np.mean([res[k]["test_mae"] for k in seeds])), 2),
           "models": res,
           "mae_difference_95ci": comparisons,
           "note": "MAE differences in events per window-day (negative = first model better). Moving-block bootstrap "
                   "over test start days: variation over days like these, not over other periods or other seeds."}
    for k, v in comparisons.items():
        print(f"{k:45s} {v['mean']:7.2f}  [{v['lo']:7.2f}, {v['hi']:7.2f}]")
    for k, v in res.items():
        print(k, v)
    write(args.out, out)


def write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, default=str))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("transformer", "select", "test", "report"), required=True)
    p.add_argument("--dataset-cache", required=True)
    p.add_argument("--checkpoints", nargs="+")
    p.add_argument("--choice", default="docs/forecast_eval/strong_baseline_select.json")
    p.add_argument("--out")
    args = p.parse_args()
    {"transformer": stage_transformer, "select": stage_select, "test": stage_test, "report": stage_report}[args.stage](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
