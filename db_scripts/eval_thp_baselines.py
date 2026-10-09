"""
Reproduce and extend the THP baseline comparison from a cached dataset.

No torch and no database: it reads the .npz dataset that train_thp_model.py
caches (default: models/thp_calibration_dataset_seq14_h7.npz) and prints the
baselines on

  * the ORIGINAL validation split (first-forecast-day assignment), so the
    numbers in the project write-up can be reproduced, and
  * the LEAK-FREE split with a held-out test partition.

If you have model predictions for a partition, pass them with
``--val-predictions`` / ``--test-predictions`` (a .npy of shape (n, horizon)
aligned to that partition's windows in dataset order) to get the headline
improvement against the strongest baseline.

Usage:
    python db_scripts/eval_thp_baselines.py
    python db_scripts/eval_thp_baselines.py --dataset models/thp_training_dataset.npz --json out.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from thp_eval_utils import (
    compare_to_baselines,
    leak_free_split,
    max_train_target_day,
    per_series_win_rate,
    seasonal_naive_from_log_features,
)

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    d = np.load(path, allow_pickle=True)
    horizon = int(d["y_count"].shape[1])
    baselines = {
        "naive_last": d["baseline_naive_last"],
        "moving_avg_7": d["baseline_moving_avg_7"],
        "empirical_hawkes": d["baseline_empirical_hawkes"],
    }
    if "baseline_seasonal_naive" in d.files:
        baselines["seasonal_naive"] = d["baseline_seasonal_naive"]
    else:
        baselines["seasonal_naive"] = seasonal_naive_from_log_features(d["x"], horizon)
    return d, baselines, horizon


def original_split(target_positions, total_days, val_fraction):
    """The split the project originally used (kept only to reproduce old numbers)."""
    split_position = int(total_days * (1.0 - val_fraction))
    return (
        np.where(target_positions < split_position)[0],
        np.where(target_positions >= split_position)[0],
        split_position,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="models/thp_calibration_dataset_seq14_h7.npz")
    ap.add_argument("--val-fraction", type=float, default=0.15)
    ap.add_argument("--test-fraction", type=float, default=0.15)
    ap.add_argument("--val-predictions", help=".npy, model predictions for the leak-free validation windows")
    ap.add_argument("--test-predictions", help=".npy, model predictions for the leak-free test windows")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()

    path = Path(args.dataset)
    path = path if path.is_absolute() else ROOT / path
    d, baselines, horizon = load(path)
    y = d["y_count"]
    pos = d["target_positions"]
    total_days = int(d["total_days"])
    labels = d["labels"]

    report = {"dataset": str(path), "windows": int(len(y)), "horizon": horizon, "total_days": total_days}

    # 1) Original split, to reproduce the published numbers
    _, val_old, cut = original_split(pos, total_days, args.val_fraction)
    report["original_validation"] = {
        "split_day_index": int(cut),
        "windows": int(len(val_old)),
        **compare_to_baselines(y[val_old], None, {k: v[val_old] for k, v in baselines.items()}),
    }

    # 2) Leak-free split
    split = leak_free_split(pos, total_days, horizon, args.val_fraction, args.test_fraction)
    report["leak_free_split"] = {
        **split.sizes(),
        "val_start_day": split.val_start,
        "test_start_day": split.test_start,
        "last_train_target_day": max_train_target_day(split, pos, horizon),
    }
    for name, idx, pred_path in (
        ("validation", split.val_idx, args.val_predictions),
        ("test", split.test_idx, args.test_predictions),
    ):
        if not len(idx):
            continue
        pred = np.load(pred_path) if pred_path else None
        block = compare_to_baselines(y[idx], pred, {k: v[idx] for k, v in baselines.items()})
        if pred is not None:
            strongest = block["strongest_baseline"]
            block["per_series_vs_strongest"] = per_series_win_rate(labels[idx], y[idx], pred, baselines[strongest][idx])
        report[name] = block

    print(json.dumps(report, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
