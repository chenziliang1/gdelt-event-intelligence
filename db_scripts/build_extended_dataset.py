#!/usr/bin/env python3
"""
Training cache for 2024 plus a later period, for the series a checkpoint already covers.

2024 rows come from the same thp_* tables and daily_summary as the original training; the later
period is rebuilt from an events table loaded by load_gdelt_period.py, with the rules that
reproduce those tables exactly (thp_series_from_events.py). The cache has the format
train_thp_model.py reads, plus first_day, so the split can be given as dates:

    python db_scripts/build_extended_dataset.py --table events_2025 --end 2025-06-11 \
        --series-from models/thp_gdelt.pt --out models/thp_dataset_2024_2025h1_seq14_h7.npz
    python db_scripts/train_thp_model.py --dataset-cache models/thp_dataset_2024_2025h1_seq14_h7.npz \
        --val-start-date 2025-02-15 --test-start-date 2025-04-01 ...

--check-against compares the 2024 windows with an older 2024-only cache (must be identical).
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
from eval_fresh_period import history_rows, new_rows  # noqa: E402
from thp_inference import Checkpoint  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--table", required=True)
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", required=True, help="Last day with complete data (GDELT has an outage from 2025-06-12 18:15).")
    p.add_argument("--series-from", required=True, help="Checkpoint whose series set is kept.")
    p.add_argument("--seq-len", type=int, default=14)
    p.add_argument("--horizon", type=int, default=7)
    p.add_argument("--out", required=True)
    p.add_argument("--check-against", help="Older 2024-only cache with the same series.")
    args = p.parse_args()

    series_ids = sorted(Checkpoint(args.series_from).series_to_id)
    rows = history_rows(series_ids) + new_rows(args.table, args.start, args.end, series_ids)
    series = T.build_series(rows, min_series_events=0)
    day0 = min(date.fromisoformat(str(r["event_date"])[:10]) for r in rows)
    x, y_log, y_count, labels, pos, baselines, total_days = T.make_dataset(series, args.seq_len, args.horizon)
    print(f"series {len(series)} windows {len(x)} days {total_days} ({day0} .. {day0 + timedelta(days=total_days - 1)})")

    if args.check_against:
        old = np.load(args.check_against, allow_pickle=False)
        old_keys = {(lbl, int(q)): i for i, (lbl, q) in enumerate(zip(old["labels"].tolist(), old["target_positions"]))}
        new_labels = [f"{a}||{b}" for a, b in labels]
        pairs = [(i, old_keys[(lbl, int(q))]) for i, (lbl, q) in enumerate(zip(new_labels, pos)) if (lbl, int(q)) in old_keys]
        a, b = zip(*pairs)
        diff = np.abs(x[list(a)] - old["x"][list(b)])
        same_x = bool(diff.max() <= 1e-4)
        same_y = np.array_equal(y_count[list(a)], old["y_count"][list(b)])
        print(f"2024 windows matched {len(pairs)} of {len(old_keys)}; x max abs diff {diff.max():.2e} "
              f"(per feature {np.round(diff.max(axis=(0, 1)), 6).tolist()}); y identical {same_y}")
        if len(pairs) != len(old_keys) or not (same_x and same_y):
            return 1
        dimension_summary = str(old["dimension_summary_json"])
        cache_meta = str(old["cache_meta_json"])
    else:
        dimension_summary = json.dumps({"series_from": args.series_from})
        cache_meta = json.dumps({"seq_len": args.seq_len, "forecast_horizon": args.horizon})

    np.savez_compressed(
        args.out,
        x=x, y_log=y_log, y_count=y_count,
        labels=np.asarray([f"{a}||{b}" for a, b in labels]),
        target_positions=pos,
        baseline_naive_last=baselines["naive_last"],
        baseline_moving_avg_7=baselines["moving_avg_7"],
        baseline_empirical_hawkes=baselines["empirical_hawkes"],
        baseline_seasonal_naive=baselines["seasonal_naive"],
        total_days=np.asarray(total_days, dtype=np.int32),
        first_day=np.asarray(day0.isoformat()),
        dimension_summary_json=np.asarray(dimension_summary),
        cache_meta_json=np.asarray(cache_meta),
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
