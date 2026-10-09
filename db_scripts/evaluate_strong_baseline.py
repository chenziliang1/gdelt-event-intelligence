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

Follow-ups (sensitivity and intervals; nothing here is chosen on test):

  pool         LightGBM only. Refits the chosen configuration with the same seed, checks that it
               reproduces the tested predictions exactly, and saves its validation and test
               predictions for the interval stage.
  seeds        LightGBM only. The chosen configuration subsamples rows and features (bagging 0.8,
               feature fraction 0.9), so the seed changes the model: refits it with seeds 42, 1, 2
               (same early stopping on validation) and reports validation and test MAE per seed,
               and the block-bootstrap interval for Transformer 3-seed mean minus LightGBM 3-seed mean.
  intervals    no LightGBM (imports evaluate_retrain). 80% intervals for LightGBM by exactly the
               Transformer's method and rule: candidates static and rolling 14/28/42-day size-binned
               log-space intervals, chosen on validation (fit on the first half, score on the second,
               smallest worst-bin gap to 80%, ties narrower), then test coverage and width.

torch and LightGBM are kept in separate processes: both link an OpenMP runtime. On macOS
LightGBM needs libomp (`brew install libomp`, or point DYLD_FALLBACK_LIBRARY_PATH at torch/lib).

    CACHE=models/thp_dataset_2024_2025h1_seq14_h7.npz
    python db_scripts/evaluate_strong_baseline.py --stage transformer --dataset-cache $CACHE \
        --checkpoints models/retrain_2025h1/seed42.pt models/retrain_2025h1/seed1.pt models/retrain_2025h1/seed2.pt
    python db_scripts/evaluate_strong_baseline.py --stage select --dataset-cache $CACHE --out docs/forecast_eval/strong_baseline_select.json
    python db_scripts/evaluate_strong_baseline.py --stage test --dataset-cache $CACHE --choice docs/forecast_eval/strong_baseline_select.json
    python db_scripts/evaluate_strong_baseline.py --stage report --dataset-cache $CACHE --out docs/forecast_eval/strong_baseline_test.json
    python db_scripts/evaluate_strong_baseline.py --stage pool --dataset-cache $CACHE
    python db_scripts/evaluate_strong_baseline.py --stage seeds --dataset-cache $CACHE --out docs/forecast_eval/strong_baseline_seeds.json
    python db_scripts/evaluate_strong_baseline.py --stage intervals --dataset-cache $CACHE --out docs/forecast_eval/strong_baseline_intervals.json
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
POOL_PREDS = WORK / "lightgbm_pool_preds.npz"
SENSITIVITY_SEEDS = (42, 1, 2)

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


def fit(cfg, d, split, day0, seed=SEED):
    import lightgbm as lgb
    tr, va = split.train_idx, split.val_idx
    Xtr, names, cat, s_tr = features(d, tr, day0, cfg["series_id"])
    Xva, _, _, s_va = features(d, va, day0, cfg["series_id"])
    params = {"objective": cfg["objective"], "learning_rate": 0.05, "num_leaves": cfg["num_leaves"],
              "min_data_in_leaf": 100, "feature_fraction": 0.9, "bagging_fraction": 0.8, "bagging_freq": 1,
              "lambda_l2": 1.0, "seed": seed, "deterministic": True, "num_threads": 8, "verbose": -1,
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
        return per_day_mae(y, pred, pos, days)

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


def per_day_mae(y, pred, pos, days):
    """MAE of each test start day (all series and horizon days of the windows starting that day)."""
    err = np.abs(np.asarray(y, dtype=np.float64) - pred).mean(axis=1)
    return np.asarray([err[pos == t].mean() for t in days])


def choose_interval_method(scores):
    """evaluate_retrain's rule: smallest worst-size-bin gap to 80% coverage, ties narrower.
    ``scores`` maps a method to (worst_bin_gap, mean_width)."""
    return min(scores, key=lambda k: scores[k])


def stage_pool(args):
    cfg = json.loads(Path(args.choice).read_text())["chosen"]
    d, split, day0 = load(args.dataset_cache)
    booster = fit(cfg, d, split, day0, SEED)
    va, tst = split.val_idx, split.test_idx
    p_va, p_tst = predict(booster, cfg, d, va, day0), predict(booster, cfg, d, tst, day0)
    tested = np.load(WORK / "lightgbm_test_preds.npz")["lightgbm"].astype(np.float64)
    diff = float(np.abs(p_tst.astype(np.float32).astype(np.float64) - tested).max())
    print(f"seed {SEED} refit: best iteration {booster.best_iteration}, test MAE {mae(d['y_count'][tst], p_tst):.2f}, "
          f"max abs difference from the tested predictions {diff}")
    np.savez_compressed(POOL_PREDS, val_idx=va, test_idx=tst, val=p_va, test=p_tst,
                        best_iteration=booster.best_iteration, max_abs_diff_vs_tested=diff)


def stage_seeds(args):
    cfg = json.loads(Path(args.choice).read_text())["chosen"]
    d, split, day0 = load(args.dataset_cache)
    va, tst = split.val_idx, split.test_idx
    y = d["y_count"].astype(np.float64)
    pos, days = d["target_positions"][tst], np.unique(d["target_positions"][tst])
    rows, test_preds = [], {}
    for seed in SENSITIVITY_SEEDS:
        t0 = time.time()
        booster = fit(cfg, d, split, day0, seed)
        p_va, p_tst = predict(booster, cfg, d, va, day0), predict(booster, cfg, d, tst, day0)
        test_preds[seed] = p_tst
        row = {"seed": seed, "best_iteration": int(booster.best_iteration),
               "validation_mae": round(mae(y[va], p_va), 2), "test_mae": round(mae(y[tst], p_tst), 2),
               "seconds": round(time.time() - t0)}
        rows.append(row)
        print(row, flush=True)
    pool = np.load(POOL_PREDS)
    tr = dict(np.load(WORK / "transformer_test_preds.npz"))
    tr_day = np.mean([per_day_mae(y[tst], v.astype(np.float64), pos, days) for v in tr.values()], axis=0)
    lgb_day = np.mean([per_day_mae(y[tst], p, pos, days) for p in test_preds.values()], axis=0)
    ci = moving_block_bootstrap_ci(tr_day - lgb_day, block=7, n_resamples=2000, seed=SEED)
    test_maes = [r["test_mae"] for r in rows]
    out = {"stage": "seeds", "config": cfg,
           "subsampling": {"bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.9},
           "determinism": {"seed": SEED, "deterministic_flag": True,
                           "refit_max_abs_diff_vs_tested": float(pool["max_abs_diff_vs_tested"]),
                           "note": "Same seed reproduces the tested model exactly; because rows and features are "
                                   "subsampled, a different seed gives a different model."},
           "tested_seed_2025": {"test_mae": round(mae(y[tst], pool["test"]), 2), "validation_mae": round(mae(y[va], pool["val"]), 2)},
           "seeds": rows,
           "test_mae_mean": round(float(np.mean(test_maes)), 2),
           "test_mae_range": [min(test_maes), max(test_maes)],
           "validation_mae_mean": round(float(np.mean([r["validation_mae"] for r in rows])), 2),
           "transformer_seed_mean_minus_lightgbm_seed_mean_95ci": {k: (round(v, 2) if isinstance(v, float) else v)
                                                                   for k, v in ci.items()},
           "note": "Sensitivity only: no choice is made on these test numbers. Same moving-block bootstrap as the "
                   "report stage (7-day blocks over the 66 test start days, 2,000 resamples)."}
    print(json.dumps({k: out[k] for k in ("test_mae_mean", "test_mae_range", "validation_mae_mean",
                                          "transformer_seed_mean_minus_lightgbm_seed_mean_95ci")}))
    write(args.out, out)


def stage_intervals(args):
    from evaluate_retrain import coverage, load as load_retrain, methods
    d, split, _, scale = load_retrain(args.dataset_cache, "2025-02-01", "2025-04-01")
    pos, y = d["target_positions"], d["y_count"].astype(np.float64)
    val, tst = split.val_idx, split.test_idx
    p = np.load(POOL_PREDS)
    assert np.array_equal(p["val_idx"], val) and np.array_equal(p["test_idx"], tst), "split differs from the pool stage"
    pred = np.zeros_like(y)
    pred[val], pred[tst] = p["val"], p["test"]
    # Validation: exactly evaluate_retrain.select.
    mid = split.val_start + (split.test_start - split.val_start) // 2
    fit_idx, eval_idx = val[pos[val] + HORIZON - 1 < mid], val[pos[val] >= mid]
    on_val = {name: coverage(y[eval_idx], lo, hi, scale[eval_idx])
              for name, (lo, hi) in methods(pred, y, scale, pos, fit_idx, val, eval_idx).items()}
    chosen = choose_interval_method({k: (r["worst_bin_gap"], r["mean_width"]) for k, r in on_val.items()})
    # Test: exactly evaluate_retrain.test (static fitted on validation; rolling refits on observed windows).
    pool = np.concatenate([val, tst])
    on_test = {name: coverage(y[tst], lo, hi, scale[tst])
               for name, (lo, hi) in methods(pred, y, scale, pos, val, pool, tst).items()}
    ref_sel = json.loads(Path("docs/forecast_eval/retrain_2025h1_select.json").read_text())
    ref_tst = json.loads(Path("docs/forecast_eval/retrain_2025h1_test.json").read_text())
    transformer = {Path(k).stem: {m: c["intervals"][m] for m in (ref_tst["interval_method"], "static")}
                   for k, c in ref_tst["checkpoints"].items() if "retrain_2025h1" in k}
    out = {"stage": "intervals", "nominal": 0.80, "fit_windows": int(len(fit_idx)), "eval_windows": int(len(eval_idx)),
           "lightgbm": {"validation_second_half": on_val, "chosen_interval_method": chosen, "test": on_test},
           "transformer_reference": {"chosen_interval_method": ref_sel["chosen_interval_method"], "test": transformer},
           "note": "Same candidates, rule and fitting as evaluate_retrain.py; the method is chosen on validation only."}
    print("LightGBM chosen interval method:", chosen)
    for name, r in on_val.items():
        print(f"  val  {name:12s} overall {r['overall']} by size {r['by_size']} worst gap {r['worst_bin_gap']} width {r['mean_width']}")
    for name, r in on_test.items():
        print(f"  test {name:12s} overall {r['overall']} by size {r['by_size']} width {r['mean_width']}")
    write(args.out, out)


def write(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=1, default=str))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("transformer", "select", "test", "report", "pool", "seeds", "intervals"),
                   required=True)
    p.add_argument("--dataset-cache", required=True)
    p.add_argument("--checkpoints", nargs="+")
    p.add_argument("--choice", default="docs/forecast_eval/strong_baseline_select.json")
    p.add_argument("--out")
    args = p.parse_args()
    {"transformer": stage_transformer, "select": stage_select, "test": stage_test, "report": stage_report,
     "pool": stage_pool, "seeds": stage_seeds, "intervals": stage_intervals}[args.stage](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
