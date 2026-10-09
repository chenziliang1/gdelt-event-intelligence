#!/usr/bin/env python3
"""
Evaluate frozen forecaster checkpoints on a period none of them has seen (Q1 2025).

The 2024 test partition had been looked at twice, so it can no longer decide anything. This
script builds windows whose 7 forecast days all fall in [--start, --end] from:
  * 2024 history: the same thp_* tables and daily_summary the model was trained on;
  * the new period: events loaded by load_gdelt_period.py, turned into the same series by
    thp_series_from_events.py (verified to reproduce the 2024 tables exactly).
No model, threshold or interval is changed here; checkpoints are only read.

    python db_scripts/eval_fresh_period.py --table events_2025 --start 2025-01-01 --end 2025-03-31 \
        --checkpoints models/thp_gdelt.pt other.pt --out docs/forecast_eval/fresh_2025q1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_thp_model as T  # noqa: E402
from thp_eval_utils import (  # noqa: E402
    compare_to_baselines,
    coverage_by_scale,
    log_interval_bounds,
    log_interval_bounds_by_size,
    per_series_win_rate,
)
from thp_inference import Checkpoint  # noqa: E402
from thp_series_from_events import actor_series, sql_series  # noqa: E402
import gdelt_raw  # noqa: E402


def history_rows(series_ids):
    """2024 rows exactly as training fetched them."""
    by_prefix = {}
    for sid in series_ids:
        prefix, _, name = sid.partition(":")
        by_prefix.setdefault(prefix, []).append(name)
    conn = T.connect_db()
    try:
        cur = conn.cursor(dictionary=True)
        rows = T.fetch_global_rows(cur)
        rows += T.fetch_country_rows(cur, by_prefix.get("country", []))
        rows += T.fetch_actor_rows(cur, by_prefix.get("actor", []))
        rows += T.fetch_country_pair_rows(cur, by_prefix.get("country_pair", []))
        rows += T.fetch_actor_pair_rows(cur, by_prefix.get("actor_pair", []))
        rows += T.fetch_event_root_rows(cur, by_prefix.get("event_root", []))
        rows += T.fetch_event_code_rows(cur, by_prefix.get("event_code", []))
        return rows
    finally:
        conn.close()


def new_rows(table, start, end, series_ids):
    wanted = set(series_ids)
    with gdelt_raw._db() as conn, conn.cursor() as cur:
        rows = sql_series(cur, table, start, end) + actor_series(cur, table, start, end)
    return [r for r in rows if r["series_id"] in wanted]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--table", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--checkpoints", nargs="+", required=True)
    p.add_argument("--horizon", type=int, default=7)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    first = Checkpoint(args.checkpoints[0])
    series_ids = sorted(first.series_to_id)
    rows = history_rows(series_ids) + new_rows(args.table, args.start, args.end, series_ids)
    present = {r["series_id"] for r in rows}
    missing = sorted(set(series_ids) - present)
    series = T.build_series(rows, min_series_events=0)
    day0 = min(date.fromisoformat(str(r["event_date"])[:10]) for r in rows)  # build_series starts here
    start_pos = (date.fromisoformat(args.start) - day0).days
    end_pos = (date.fromisoformat(args.end) - day0).days

    results = {"period": [args.start, args.end], "table": args.table, "series_missing_in_data": missing, "models": {}}
    datasets = {}
    for path in args.checkpoints:
        ck = Checkpoint(path)
        if ck.seq_len not in datasets:
            x, _, y, labels, pos, baselines, _ = T.make_dataset(series, ck.seq_len, args.horizon)
            keep = np.where((pos >= start_pos) & (pos + args.horizon - 1 <= end_pos))[0]
            datasets[ck.seq_len] = (x[keep], y[keep], [labels[i] for i in keep],
                                    {k: v[keep] for k, v in baselines.items()})
        x, y, labels, baselines = datasets[ck.seq_len]
        pred = ck.predict(x, labels, args.horizon)
        cmp = compare_to_baselines(y, pred, baselines)
        strongest = cmp["strongest_baseline"]
        cmp["windows"] = int(len(y))
        cmp["per_series_vs_strongest"] = per_series_win_rate(
            np.asarray([f"{a}||{b}" for a, b in labels]), y, pred, baselines[strongest])
        scale = np.expm1(x[:, :, 0]).mean(axis=1)
        scale_h = np.repeat(scale[:, None], args.horizon, axis=1)
        cal = ck.raw.get("calibration", {})
        coverage = {}
        if cal.get("log_interval"):
            lo, hi = log_interval_bounds(pred, cal["log_interval"])
            inside = (y >= lo) & (y <= hi)
            coverage["log_pooled"] = {"overall": round(float(inside.mean()), 3), "by_size": coverage_by_scale(y, inside, scale_h)}
        if cal.get("log_interval_by_size"):
            lo, hi = log_interval_bounds_by_size(pred, scale, cal["log_interval_by_size"])
            inside = (y >= lo) & (y <= hi)
            coverage["log_by_size"] = {"overall": round(float(inside.mean()), 3), "by_size": coverage_by_scale(y, inside, scale_h),
                                       "mean_width": round(float((hi - lo).mean()), 1)}
        cmp["interval_coverage_80"] = coverage
        results["models"][path] = cmp
        print(f"{Path(path).name}: seq {ck.seq_len} windows {len(y)} model MAE {cmp['model_mae']} "
              f"strongest {strongest} {cmp['baseline_mae'][strongest]} improvement {cmp['improvement_pct_vs_strongest']}% "
              f"series win {cmp['per_series_vs_strongest']['win_rate']} coverage {json.dumps({k: v['overall'] for k, v in coverage.items()})}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=1, default=str))
    print("missing series:", missing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
