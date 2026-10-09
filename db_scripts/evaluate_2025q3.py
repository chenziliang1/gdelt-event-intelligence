#!/usr/bin/env python3
"""
Which forecaster to serve, decided on 2025 Q3 by the rule fixed beforehand
(docs/forecast_eval/2025q3_decision_rule.md, committed before any Q3 data was loaded).

Frozen models, no retraining or tuning: the three retrained Transformer checkpoints, and LightGBM
with the configuration chosen on validation refitted with the tested seed (it reproduces the tested
model; checked here). Stages, run in order (torch and LightGBM in separate processes, as in
evaluate_strong_baseline.py):

  build        the Q3 windows from events_2025 (2025-07-03..09-30), features built only from data
               inside the period: the rolling features look back up to 30 days, and the days before
               2025-07-03 are the GDELT outage, not zero events. Checks the same code path against the
               training cache on Q2 (targets and non-rolling features must match).
  transformer  predictions of each checkpoint.
  lightgbm     refits the chosen configuration (seed 2025), checks it reproduces the tested Q2
               predictions, predicts Q3.
  report       MAE, win rates, bootstrap interval, rolling 14-day 80% intervals, and the decision.

    python db_scripts/evaluate_2025q3.py --stage build
    python db_scripts/evaluate_2025q3.py --stage transformer
    DYLD_FALLBACK_LIBRARY_PATH=<torch>/lib python db_scripts/evaluate_2025q3.py --stage lightgbm
    python db_scripts/evaluate_2025q3.py --stage report --out docs/forecast_eval/2025q3_test.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp_eval_utils import mae, moving_block_bootstrap_ci, per_series_win_rate  # noqa: E402

START, END = "2025-07-03", "2025-09-30"
TABLE = "events_2025"
HORIZON, SEQ = 7, 14
SEED = 2025
CHECKPOINTS = ["models/retrain_2025h1/seed42.pt", "models/retrain_2025h1/seed1.pt", "models/retrain_2025h1/seed2.pt"]
SERVED = "seed2"
TRAIN_CACHE = "models/thp_dataset_2024_2025h1_seq14_h7.npz"
WORK = Path("models/strong_baseline")  # gitignored; everything here is rebuilt by this script
Q3_CACHE = WORK / "q3_cache.npz"
COVERAGE_FLOOR = 0.75
ROLLING_DAYS = 14
EXCLUDED_FIRST_DAYS = 14


def _rows(start, end, series_ids):
    from eval_fresh_period import new_rows
    rows = new_rows(TABLE, start, end, series_ids)
    present = {r["series_id"] for r in rows}
    # A series with no event in the period still exists: it had zero events every day.
    missing = sorted(set(series_ids) - present)
    rows += [{"series_id": sid, "event_date": date.fromisoformat(start)} for sid in missing]
    return rows, missing


def _windows(rows):
    import train_thp_model as T
    series = T.build_series(rows, min_series_events=0)
    return T.make_dataset(series, SEQ, HORIZON)


def stage_build(args):
    from thp_inference import Checkpoint
    series_ids = sorted(Checkpoint(CHECKPOINTS[0]).series_to_id)
    rows, missing = _rows(START, END, series_ids)
    x, y_log, y_count, labels, pos, baselines, total_days = _windows(rows)
    lab = [f"{a}||{b}" for a, b in labels]
    print(f"Q3: series {len(series_ids)} ({len(missing)} with no event), labels {len(set(lab))}, "
          f"windows {len(x)}, days {total_days}")

    # Same code path on Q2 against the training cache: the targets and the features that do not
    # depend on history (first 10: counts, shares, Goldstein, tone, articles, calendar) must match.
    q2_rows, _ = _rows("2025-04-01", "2025-06-11", series_ids)
    x2, _, y2, labels2, pos2, _, _ = _windows(q2_rows)
    old = np.load(TRAIN_CACHE, allow_pickle=False)
    old_day0 = date.fromisoformat(str(old["first_day"]))
    q2_day0 = date(2025, 4, 1)
    offset = (q2_day0 - old_day0).days
    old_keys = {(l, int(p)): i for i, (l, p) in enumerate(zip(old["labels"].tolist(), old["target_positions"]))}
    pairs = [(i, old_keys[(f"{a}||{b}", int(p) + offset)]) for i, ((a, b), p) in enumerate(zip(labels2, pos2))
             if (f"{a}||{b}", int(p) + offset) in old_keys]
    a, b = map(list, zip(*pairs))
    same_y = bool(np.array_equal(y2[a], old["y_count"][b]))
    x_diff = float(np.abs(x2[a][:, :, :10] - old["x"][b][:, :, :10]).max())
    check = {"q2_windows_compared": len(pairs), "q2_windows_built": int(len(x2)), "targets_identical": same_y,
             "non_rolling_features_max_abs_diff": x_diff}
    print("Q2 check against the training cache:", check)
    if not same_y or x_diff > 1e-4:
        raise SystemExit("Q2 check failed")

    WORK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        Q3_CACHE, x=x, y_log=y_log, y_count=y_count, labels=np.asarray(lab), target_positions=pos,
        baseline_seasonal_naive=baselines["seasonal_naive"], total_days=np.asarray(total_days, dtype=np.int32),
        first_day=np.asarray(START), series_without_events_json=np.asarray(json.dumps(missing)),
        build_check_json=np.asarray(json.dumps(check)))
    print(f"wrote {Q3_CACHE}")


def _q3():
    d = np.load(Q3_CACHE, allow_pickle=False)
    return d, date.fromisoformat(str(d["first_day"]))


def stage_transformer(args):
    from evaluate_retrain import predict_all
    d, _ = _q3()
    labels = [tuple(x.split("||", 1)) for x in d["labels"].tolist()]
    idx = np.arange(len(d["x"]))
    preds = {}
    for path in CHECKPOINTS:
        preds[Path(path).stem] = predict_all(path, d, labels, idx).astype(np.float32)
        print(f"{path}: Q3 MAE {mae(d['y_count'], preds[Path(path).stem]):.2f}")
    np.savez_compressed(WORK / "q3_transformer.npz", **preds)


def stage_lightgbm(args):
    from evaluate_strong_baseline import fit, load, predict
    cfg = json.loads(Path("docs/forecast_eval/strong_baseline_select.json").read_text())["chosen"]
    d_tr, split, day0_tr = load(TRAIN_CACHE)
    booster = fit(cfg, d_tr, split, day0_tr, SEED)
    tested = np.load(WORK / "lightgbm_test_preds.npz")["lightgbm"].astype(np.float64)
    p_q2 = predict(booster, cfg, d_tr, split.test_idx, day0_tr).astype(np.float32).astype(np.float64)
    diff = float(np.abs(p_q2 - tested).max())
    print(f"refit seed {SEED}: best iteration {booster.best_iteration}; max abs difference from the tested Q2 "
          f"predictions {diff}")
    if diff > 1e-3:
        raise SystemExit("the refit does not reproduce the tested model")
    d, day0 = _q3()
    # The categorical codes (series kind, measure) are built from each cache's labels: they must agree.
    kinds = lambda labels: sorted({s.split(":", 1)[0] for s in labels})  # noqa: E731
    measures = lambda labels: sorted({s.split("||", 1)[1] for s in labels})  # noqa: E731
    assert kinds(d_tr["labels"]) == kinds(d["labels"]) and measures(d_tr["labels"]) == measures(d["labels"])
    p = predict(booster, cfg, d, np.arange(len(d["x"])), day0)
    np.savez_compressed(WORK / "q3_lightgbm.npz", lightgbm=p.astype(np.float32), refit_max_abs_diff=diff,
                        best_iteration=booster.best_iteration)
    print(f"LightGBM Q3 MAE {mae(d['y_count'], p):.2f}")


def stage_report(args):
    from evaluate_retrain import coverage, rolling_bounds
    from evaluate_strong_baseline import per_day_mae
    d, day0 = _q3()
    y = d["y_count"].astype(np.float64)
    naive = d["baseline_seasonal_naive"].astype(np.float64)
    pos = d["target_positions"]
    labels = np.asarray(d["labels"])
    scale = np.expm1(d["x"][:, :, 0]).mean(axis=1)
    days = np.unique(pos)
    models = {f"transformer_{k}": v.astype(np.float64) for k, v in np.load(WORK / "q3_transformer.npz").items()}
    models["lightgbm"] = np.load(WORK / "q3_lightgbm.npz")["lightgbm"].astype(np.float64)
    seeds = [k for k in models if k.startswith("transformer_")]

    allidx = np.arange(len(y))
    eligible = allidx[pos >= days[0] + EXCLUDED_FIRST_DAYS]
    res = {}
    for name, p in models.items():
        lo, hi = rolling_bounds(p, y, scale, pos, allidx, eligible, ROLLING_DAYS)
        res[name] = {"mae": round(mae(y, p), 2),
                     "improvement_pct_vs_seasonal_naive": round(100 * (1 - mae(y, p) / mae(y, naive)), 1),
                     "per_series_vs_seasonal_naive": per_series_win_rate(labels, y, p, naive),
                     "interval_80_rolling_14d": coverage(y[eligible], lo, hi, scale[eligible])}
    day = {k: per_day_mae(y, p, pos, days) for k, p in models.items()}
    day["seasonal_naive"] = per_day_mae(y, naive, pos, days)
    day["transformer_seed_mean"] = np.mean([day[k] for k in seeds], axis=0)

    def ci(a, b):
        r = moving_block_bootstrap_ci(day[a] - day[b], block=7, n_resamples=2000, seed=SEED)
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}

    tr_mae = float(np.mean([res[k]["mae"] for k in seeds]))
    tr_cov = float(np.mean([res[k]["interval_80_rolling_14d"]["overall"] for k in seeds]))
    lg_mae, lg_cov = res["lightgbm"]["mae"], res["lightgbm"]["interval_80_rolling_14d"]["overall"]
    diff_ci = ci("transformer_seed_mean", "lightgbm")

    # The rule, step by step (docs/forecast_eval/2025q3_decision_rule.md).
    steps = []
    candidates = {"transformer": (tr_mae, tr_cov), "lightgbm": (lg_mae, lg_cov)}
    left = {k: v for k, v in candidates.items() if v[1] >= COVERAGE_FLOOR}
    steps.append(f"1. coverage floor {COVERAGE_FLOOR}: transformer {tr_cov:.3f} (mean of 3 seeds), lightgbm "
                 f"{lg_cov:.3f}; eligible: {sorted(left) or 'none'}")
    if not left:
        decision = "none eligible: neither model may be served under the rule"
    elif len(left) == 1:
        decision = next(iter(left))
        steps.append(f"2. only {decision} is eligible")
    else:
        lower = min(left, key=lambda k: left[k][0])
        steps.append(f"2. lower MAE: transformer {tr_mae:.2f} vs lightgbm {lg_mae:.2f} -> {lower}")
        includes_zero = diff_ci["lo"] <= 0 <= diff_ci["hi"]
        steps.append(f"3. 95% interval of transformer minus lightgbm [{diff_ci['lo']}, {diff_ci['hi']}] "
                     f"{'includes' if includes_zero else 'excludes'} 0")
        decision = "lightgbm" if includes_zero else lower
    if decision == "transformer":
        steps.append("4. the served checkpoint stays seed 2")

    out = {"stage": "report", "rule": "docs/forecast_eval/2025q3_decision_rule.md (commit afa1791)",
           "period": [START, END],
           "forecast_start_days": [str(day0 + timedelta(days=int(days[0]))), str(day0 + timedelta(days=int(days[-1])))],
           "windows": int(len(y)), "start_days": int(len(days)),
           "interval_windows": int(len(eligible)), "interval_first_day": str(day0 + timedelta(days=int(days[0]) + EXCLUDED_FIRST_DAYS)),
           "build_check": json.loads(str(d["build_check_json"])),
           "series_without_events": json.loads(str(d["series_without_events_json"])),
           "seasonal_naive_mae": round(mae(y, naive), 2),
           "transformer_seed_mean": {"mae": round(tr_mae, 2), "interval_80_overall": round(tr_cov, 3)},
           "served_transformer_seed": SERVED,
           "models": res,
           "mae_difference_95ci": {
               "transformer_seed_mean_minus_lightgbm": diff_ci,
               "transformer_seed_mean_minus_seasonal_naive": ci("transformer_seed_mean", "seasonal_naive"),
               "lightgbm_minus_seasonal_naive": ci("lightgbm", "seasonal_naive"),
               **{f"{k}_minus_lightgbm": ci(k, "lightgbm") for k in seeds}},
           "decision_steps": steps, "decision": decision,
           "note": "MAE in events per window-day. Moving-block bootstrap over forecast start days (7-day blocks, "
                   "2,000 resamples). Intervals: rolling 14-day refit on windows whose targets are observed before "
                   "the forecast day; the first 14 forecast days are excluded from coverage (no history)."}
    for s in steps:
        print(s)
    print("DECISION:", decision)
    for k, v in res.items():
        print(k, v["mae"], v["improvement_pct_vs_seasonal_naive"], v["per_series_vs_seasonal_naive"].get("win_rate"),
              v["interval_80_rolling_14d"]["overall"], v["interval_80_rolling_14d"]["by_size"], v["interval_80_rolling_14d"]["mean_width"])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("build", "transformer", "lightgbm", "report"), required=True)
    p.add_argument("--out", default="docs/forecast_eval/2025q3_test.json")
    a = p.parse_args()
    {"build": stage_build, "transformer": stage_transformer, "lightgbm": stage_lightgbm, "report": stage_report}[a.stage](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
