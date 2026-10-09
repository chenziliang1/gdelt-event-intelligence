#!/usr/bin/env python3
"""
Fit size-binned 80% intervals for a trained checkpoint, on its VALIDATION windows only, and
store them in the checkpoint (calibration["log_interval_by_size"]).

    python db_scripts/calibrate_intervals.py --checkpoint models/thp_gdelt.pt \
        --dataset-cache models/thp_calibration_dataset_seq14_h7.npz

Chosen on validation alone: fitted on the first half of the validation days and checked on the
second half, binned coverage was 0.81 / 0.82 / 0.80 / 0.83 by series size (pooled: 0.79 / 0.65 /
0.89 / 0.99) at less than half the width. The test partition is reported but not used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thp_eval_utils import (  # noqa: E402
    coverage_by_scale,
    fit_log_interval_by_size,
    leak_free_split,
    log_interval_bounds_by_size,
)
from thp_inference import Checkpoint  # noqa: E402


def window_scale(x: np.ndarray) -> np.ndarray:
    """Mean daily events over each window's input days (feature 0 is log1p(count))."""
    return np.expm1(x[:, :, 0]).mean(axis=1)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--dataset-cache", required=True)
    p.add_argument("--horizon", type=int, default=7)
    args = p.parse_args()

    d = np.load(args.dataset_cache, allow_pickle=False)
    labels = [tuple(x.split("||", 1)) for x in d["labels"].tolist()]
    split = leak_free_split(d["target_positions"], int(d["total_days"]), args.horizon, 0.15, 0.15)
    ck = Checkpoint(args.checkpoint)

    val = split.val_idx
    pred_val = ck.predict(d["x"][val], [labels[i] for i in val], args.horizon)
    fitted = fit_log_interval_by_size(d["y_count"][val], pred_val, window_scale(d["x"][val]))

    test = split.test_idx
    pred_test = ck.predict(d["x"][test], [labels[i] for i in test], args.horizon)
    lo, hi = log_interval_bounds_by_size(pred_test, window_scale(d["x"][test]), fitted)
    y = d["y_count"][test]
    inside = (y >= lo) & (y <= hi)
    scale = np.repeat(window_scale(d["x"][test])[:, None], args.horizon, axis=1)
    report = {"test_coverage": round(float(inside.mean()), 3),
              "test_coverage_by_size": coverage_by_scale(y, inside, scale),
              "test_mean_width": round(float((hi - lo).mean()), 1)}

    raw = ck.raw
    raw.setdefault("calibration", {})["log_interval_by_size"] = fitted
    raw["calibration"]["log_interval_by_size_report"] = report
    raw.setdefault("metadata", {}).setdefault("evaluation", {})["interval_by_size"] = report
    torch.save(raw, args.checkpoint)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
