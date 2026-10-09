# Forecast evaluation

How good is the 7-day event-count forecaster, measured so that the answer can be trusted. Raw results:
`docs/forecast_eval/fresh_2025q1.json` (a period no model had seen), `docs/forecast_eval/tuning_2026-10-08.json`
(tuning and final model) and `docs/forecast_eval/retrain_2026-10-08.json` (first leak-free retrain of the original design).

## Short answer

On **Q1 2025, a period none of the models had seen** (61,824 windows, built from the official GDELT files with the
same rules as the training data), with every model frozen and the choice of model made beforehand on validation:

* The served model **beats seasonal-naive** (same weekday last week) by **5.7%** MAE on average over 3 seeds
  (77.92 vs 82.62; per seed +8.1%, +4.7%, +4.3%) and wins on 65 to 69% of the 736 series. On the last 55 days of 2024
  (the held-out test used during development) it was +4.9% (98.54 vs 103.58).
* It is a **learned correction on top of seasonal-naive**: the network predicts log1p(count) minus log1p(same weekday
  last week). The original design (log counts with a Hawkes-style head) was **6.4% worse** than seasonal-naive on
  Q1 2025, with one seed 20.9% worse. Without the head, log counts did as well on average as the served model
  (77.96) but varied far more between seeds (73.7 to 83.3, against 75.9 to 79.1): the residual target mainly buys
  stability.
* The **Hawkes-style residual head was removed**: on held-out data it was worse in every comparison (2024 test,
  all 3 seeds; Q1 2025, mean 87.87 with it vs 77.96 without, log target). On validation (one seed) it helped slightly
  with the log target (63.57 vs 65.44) and hurt with the residual target (57.58 vs 56.41); the configuration selected
  on validation, the best of eight, had no head.
* **Intervals:** 80% intervals are now fitted per series-size bin on validation. On Q1 2025 they cover 0.78 / 0.78 /
  0.76 / 0.72 by size (one pooled interval: 0.77 / 0.61 / 0.84 / 0.97) at about half the width; overall 0.75 to 0.77,
  still below 0.80, worst for the largest series.
* **A train/serve mismatch was fixed:** the serving query counted protest as CAMEO roots 14 to 16 while the model was
  trained on root 14 (about twice the count), so protest forecasts were fed the wrong input.

The earlier headline, "MAE 167.74 to 83.77, about 50%", was a validation number. It compared against the weakest useful
baseline, the model's training windows overlapped the validation period, and the checkpoint was the best of an 8-trial
sweep chosen on that same validation set (`models/thp_sweeps/sweep_20260504_032159_2024_only/leaderboard.md`, trial 5).

## What changed in the evaluation

| Problem | Before | Now |
| :--- | :--- | :--- |
| Window leakage | Windows were assigned by first forecast day, so training targets ran up to 6 days into validation | A window belongs to a partition only if its whole 7-day target span is inside it; 8,832 straddling windows are dropped (`db_scripts/thp_eval_utils.leak_free_split`) |
| No test set | Validation drove early stopping, the sweep choice and calibration, and was reported as the result | Train / validation / test, 70 / 15 / 15 by day. Test is evaluated once, after everything else is fixed |
| Weak baseline | Flat 7-day average (167.74) | Seasonal-naive added (103.58 on the same windows); the headline is reported against the strongest baseline |
| Aggregate MAE only | One number dominated by the largest series | Also the share of the 736 series on which the model beats the strongest baseline |
| Intervals never checked | Residual quantiles fitted on validation, no coverage check | Coverage measured on test |
| Hawkes head never isolated | Always on at weight 0.25 | `--hawkes-residual-weight 0` trains the same model without it |

The test partition (days 311 to 365) is exactly the old validation partition: 36,064 windows. So the baseline numbers
below are directly comparable with the old ones.

## First leak-free retrain of the original design

The original model (predicting log counts, Hawkes head 0.25) and its ablation, retrained on the leak-free split with
the hyperparameters of the original sweep: seq_len 14, d_model 96, 3 layers, 4 heads, lr 0.0007, batch 1024, up to
60 epochs with early stopping on validation MAE (patience 8), CPU.

Baselines on the test windows: last value 202.13, 7-day average 167.74, Hawkes heuristic 168.44, **seasonal-naive 103.58**.

| Hawkes head weight | Seed | Test MAE | vs seasonal-naive | vs 7-day average | Series won vs seasonal-naive | 80% interval coverage |
| :---: | :---: | ---: | ---: | ---: | ---: | ---: |
| 0.25 (original) | 42 | 123.75 | -19.5% | +26.2% | 56.1% | 0.755 |
| 0.25 (original) | 1 | 104.50 | -0.9% | +37.7% | 59.9% | 0.751 |
| 0.25 (original) | 2 | 111.16 | -7.3% | +33.7% | 54.6% | 0.761 |
| 0 (ablation) | 42 | 106.35 | -2.7% | +36.6% | 65.2% | 0.760 |
| 0 (ablation) | 1 | 100.15 | +3.3% | +40.3% | 68.2% | 0.746 |
| 0 (ablation) | 2 | 99.76 | +3.7% | +40.5% | 68.6% | 0.754 |

Positive percentages mean the model is better than the baseline.

## Tuning and final model (2026-10-08)

Tuning used `--skip-test`, so these runs never computed test metrics; selection is by validation MAE only
(validation seasonal-naive: 61.30). Seed 42, seq 14, batch 1024, early stopping on validation MAE (patience 8), CPU.

| Run | Target | Hawkes head | Size, lr | Validation MAE | vs seasonal-naive |
| :--- | :--- | :---: | :--- | ---: | ---: |
| original design | log count | 0.25 | d96 L3, 7e-4 | 63.57 | -3.7% |
| | log count | 0 | d96 L3, 7e-4 | 65.44 | -6.8% |
| | seasonal residual | 0.25 | d96 L3, 7e-4 | 57.58 | +6.1% |
| | seasonal residual | 0 | d96 L3, 7e-4 | 56.41 | +8.0% |
| | seasonal residual | 0 | d96 L3, 3e-4 | 56.33 | +8.1% |
| | seasonal residual | 0 | d96 L3, 1e-4 | 57.90 | +5.5% |
| **selected** | seasonal residual | 0 | **d64 L2, 7e-4** | **56.16** | **+8.4%** |
| | seasonal residual | 0 | d48 L2, 3e-4 | 57.72 | +5.8% |

The selected configuration, 3 seeds, test evaluated once (test seasonal-naive: 103.58):

| Seed | Test MAE | vs seasonal-naive | Series won | 80% interval, original | 80% interval, log-space |
| :---: | ---: | ---: | ---: | ---: | ---: |
| 42 | 97.53 | +5.8% | 64.9% | 0.753 | 0.739 |
| 1 | 99.56 | +3.9% | 64.4% | 0.761 | 0.729 |
| 2 | 98.52 | +4.9% | 57.7% | 0.759 | 0.728 |
| **mean** | **98.54** | **+4.9%** | **62.3%** | 0.758 | 0.732 |

The seed spread (2 MAE points) is now smaller than the gap to seasonal-naive (5 points); in the first retrain of the
original design it was 19 points. Per category (improvement over seasonal-naive, seeds 42 / 1 / 2): actor pairs
+10.1 / +11.1 / +9.7%, actors +8.3 / +5.5 / +7.1%, country pairs +7.8 / +7.4 / +3.0%, event codes +6.4 / +4.4 / +3.6%,
event roots +6.3 / +3.9 / +4.0%, countries +1.7 / +2.3 / +4.5%, global +4.1 / +0.2 / +3.6%. The gain is smallest on
the few largest series, where seasonal-naive is already strong, and those dominate aggregate MAE.

Interval coverage on test by series size (mean daily events in the 14-day history), seed 42:

| Series size | Original (additive) | Log-space |
| :--- | ---: | ---: |
| under 10 | 1.00 | 0.78 |
| 10 to 100 | 0.92 | 0.57 |
| 100 to 1,000 | 0.55 | 0.77 |
| over 1,000 | 0.16 | 0.87 |

## Fresh test period: Q1 2025

The 2024 test partition had been looked at twice during development, so it could no longer decide anything. The
fresh period was built so that nothing about it could have influenced the models:

* **Data.** 4,638,688 events from the official GDELT 2.0 files (English and translated streams), US / CA / MX, event
  date 2025-01-01 to 2025-03-31, batches read until 2025-04-07 to include late additions
  (`db_scripts/load_gdelt_period.py`, table `events_2025`). The same rule reproduces a 2024 day of `events_table`
  exactly (61,009 of 61,009 rows, every column; `db_scripts/gdelt_raw.py --verify`). All 90 days are present; the six
  missing batch files are missing upstream (HTTP 404).
* **Series.** The script that built the `thp_*` training tables is not in the repository; it was reconstructed
  (`db_scripts/thp_series_from_events.py`) and reproduces those tables exactly on five 2024 days spread over the
  year (every series, every column). Two findings on the way: actors were counted per appearance (an event with the
  same actor on both sides counts twice) and without the alias table ("U.S." is not "UNITED STATES"); the global
  series uses Goldstein < 0 / > 0, not the |Goldstein| > 5 of the repository's daily ETL (now aligned).
* **Protocol.** Windows whose 7 forecast days all fall in Q1 2025; inputs reach back into December 2024. Checkpoints
  are only read (`db_scripts/eval_fresh_period.py`). The served configuration was fixed on validation before this
  run (see input window length below); the other rows are references, not candidates.

Seasonal-naive on these windows: 82.62.

| Model | Seed 42 / 1 / 2 | Mean | vs seasonal-naive | Series won |
| :--- | :--- | ---: | ---: | ---: |
| **Served: seasonal residual, no Hawkes head, 14-day input** | 75.91 / 78.77 / 79.07 | **77.92** | **+5.7%** | 65 to 69% |
| Same, 30-day input | 78.94 / 77.98 / 80.13 | 79.02 | +4.4% | 62 to 66% |
| Original design: log counts, Hawkes head 0.25 | 99.89 / 81.18 / 82.55 | 87.87 | -6.4% | 49 to 62% |
| Log counts, no Hawkes head | 83.25 / 73.66 / 76.98 | 77.96 | +5.6% | 63 to 66% |

## Input window length

Chosen on validation (`--skip-test`), 3 seeds each for the two best: 14 days 56.16 / 55.33 / 54.89 (mean 55.46),
30 days 55.22 / 55.55 / 54.99 (mean 55.25), 60 days 56.70 (one seed). The 0.2 difference is inside the seed spread,
so the simpler, already-served 14-day model was kept; that decision was recorded before the Q1 2025 run, where the
30-day model came out slightly worse (79.02 vs 77.92).

## Intervals by series size

One set of log-space quantiles gives every series the same relative width, which over-covers some sizes and
under-covers others. Quantiles per size bin (mean daily events over the input window: under 10, 10 to 100, 100 to
1,000, over 1,000) were chosen on validation alone: fitted on the first half of the validation days, they covered
0.81 / 0.82 / 0.80 / 0.83 on the second half (pooled: 0.79 / 0.65 / 0.89 / 0.99) at less than half the width
(`db_scripts/calibrate_intervals.py`). Served since 2026-10-09.

| Coverage of the 80% interval | 2024 test (Nov-Dec, seed 42) | Q1 2025 (seed 42) |
| :--- | :--- | :--- |
| One pooled interval, by size | 0.78 / 0.57 / 0.77 / 0.87 | 0.77 / 0.61 / 0.84 / 0.97 |
| Per size bin, by size | 0.80 / 0.73 / 0.68 / 0.63 | 0.78 / 0.78 / 0.76 / 0.72 |
| Overall (per size bin) / mean width | 0.73 / 220 | 0.77 / 233 |

The binned interval is far more even and half as wide, but still under-covers when the forecast period is more
volatile than validation: the 2024 test period (November and December, the US election) did that most. It is not a
guarantee of 80%.

## Caveats

* Three seeds for the final model; the spread (97.5 to 99.6) is smaller than the gap to seasonal-naive, but a
  5% gain is modest and specific to this period.
* Q1 2025 is one quarter, three months after the training data ends; it says nothing about longer horizons of
  drift. Reconstructed series match the 2024 tables exactly on the days checked, not on every day.
* The 2024 test period is not pristine. It had been evaluated once before, in the first retrain above, and that result
  (seasonal-naive is hard to beat) is what suggested predicting a correction to it. The choice among the eight
  configurations used validation only. A fresh period (2025) is the clean check.
* Only seq 14 was tuned (the cached dataset); the grid was small (8 runs).
* The training data ends on day 255 (September 12), so the test period is further away than in the original
  set-up. This is the honest set-up for a forecast, but not the same experiment as the original 83.77.
* The original 83.77 also included a post-hoc bias correction fitted on validation and reported on validation. Here
  the correction is fitted on validation and checked on test, where it did not help.
* CPU training; batch size of the original GPU run was not recorded.

## What can honestly be said

> A Transformer forecaster for 7-day event counts across 736 series, trained as a correction on top of a
> seasonal-naive baseline. On a later quarter it had never seen (Q1 2025, rebuilt from the official GDELT files), it
> cuts MAE by about 6% against seasonal-naive (3 seeds, 4 to 8%) and beats it on about two thirds of the series.
> The Hawkes-style component of the original design hurt and was removed; the 80% intervals cover 75 to 77%.

## Reproduce

```bash
# Baselines on the old split and on the leak-free split (no torch, no database):
python db_scripts/eval_thp_baselines.py

# One training run (needs torch; uses the cached dataset, no database):
python db_scripts/train_thp_model.py \
  --dataset-cache models/thp_calibration_dataset_seq14_h7.npz --seq-len 14 --forecast-horizon 7 \
  --top-countries 3 --top-actors 50 --top-country-pairs 30 --top-actor-pairs 30 \
  --top-event-roots 20 --top-event-codes 50 --min-series-events 10 \
  --epochs 60 --batch-size 1024 --d-model 96 --layers 3 --heads 4 --lr 0.0007 \
  --early-stopping-patience 8 --seed 42 \
  --hawkes-residual-weight 0.25   # 0 for the ablation

# Q1 2025, a period no model saw (needs MySQL; downloads about 3 GB from data.gdeltproject.org):
python db_scripts/load_gdelt_period.py --start 2025-01-01 --end 2025-03-31 --added-until 2025-04-07 --table events_2025
python db_scripts/eval_fresh_period.py --table events_2025 --start 2025-01-01 --end 2025-03-31 \
  --checkpoints models/thp_gdelt.pt --out docs/forecast_eval/fresh_2025q1.json

# The selected model (add --skip-test while tuning, so selection never sees test):
python db_scripts/train_thp_model.py \
  --dataset-cache models/thp_calibration_dataset_seq14_h7.npz --seq-len 14 --forecast-horizon 7 \
  --top-countries 3 --top-actors 50 --top-country-pairs 30 --top-actor-pairs 30 \
  --top-event-roots 20 --top-event-codes 50 --min-series-events 10 \
  --epochs 60 --batch-size 1024 --d-model 64 --layers 2 --heads 4 --lr 0.0007 \
  --early-stopping-patience 8 --seed 42 \
  --target-mode seasonal_residual --hawkes-residual-weight 0
```

The script prints a `TEST (held out)` line and stores the full evaluation in the checkpoint metadata and the training log.

## Next steps

1. Intervals under drift: widen with recent residuals (rolling or adaptive conformal) so a volatile period does not
   drop coverage to 0.63 for the largest series.
2. Q1 2025 has now been used once; the next model change needs a later period (Q2 2025) for its final check.
3. Retrain on 2024 plus Q1 2025 before serving 2025 forecasts; the served model has seen no 2025 data.

## Served model

`models/thp_gdelt.pt` is the selected model, seed 42 (2024 test MAE 97.53, Q1 2025 MAE 75.91), since 2026-10-08,
with size-binned intervals added on 2026-10-09. It covers the same 184
series and 4 event types as the original checkpoint. The backend adds the model output to log1p(same weekday last
week) from the input window, serves log-space intervals, and reports the comparison against the strongest baseline
on test (`/api/v1/data/forecast` -> `baseline_comparison`: seasonal_naive, +5.8%). The previous checkpoint is in git
history.
