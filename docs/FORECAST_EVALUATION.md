# Forecast evaluation

How good is the 7-day event-count forecaster, measured so that the answer can be trusted. Raw results:
`docs/forecast_eval/retrain_2025h1_{select,test}.json` (retrain on 2024 plus early 2025, tested on Q2 2025),
`docs/forecast_eval/strong_baseline_{select,test}.json` (LightGBM on the same split, and bootstrap intervals),
`docs/forecast_eval/fresh_2025q1.json` (a period no model had seen), `docs/forecast_eval/tuning_2026-10-08.json`
(tuning and final model) and `docs/forecast_eval/retrain_2026-10-08.json` (first leak-free retrain of the original design).

## Latest: retrained on 2024 plus early 2025, tested on Q2 2025 (2026-10-09)

The served model had seen no 2025 data, and Q1 2025 had already been used once as a test. So:

* **Data.** The 2024 series plus 2025-01-01 to 2025-06-11 rebuilt from `events_2025` with the rules that reproduce
  the training tables (`db_scripts/build_extended_dataset.py`; its 2024 windows are identical to the original cache,
  254,656 of 254,656). The test stops on 2025-06-11 because GDELT's own files are missing from 2025-06-12 18:15 to
  2025-07-02 (`docs/DATA_LAYER.md`).
* **Split by date.** Training targets up to 2025-01-31, validation 2025-02-01 to 03-31 (39,008 windows), test
  2025-04-01 to 06-11 (48,576 windows), leak-free as before (`--val-start-date`, `--test-start-date`).
* **Model fixed in advance.** The configuration selected on 2026-10-08 (seasonal residual, no Hawkes head, d64 L2,
  lr 7e-4, 14-day input), not re-tuned; 3 seeds; trained with `--skip-test`.
* **Intervals chosen on validation only** (`db_scripts/evaluate_retrain.py --stage select`): fitted on the first half
  of the validation days, scored on the second half. Candidates: the static size-binned interval served until now,
  and rolling size-binned intervals refitted at each forecast day on the windows observed in the previous 14, 28 or
  42 days. Rule: the worst size bin closest to 80%. Rolling 14 days won (worst bin off by 0.019 to 0.028, static
  0.034 to 0.053). The served seed was fixed the same way: best validation MAE.
* **Test run once** (`--stage test`), with the 2024-only model as a reference.

Seasonal-naive on the test windows: 62.05.

| Model | Validation MAE (seasonal-naive 67.19) | Test MAE | vs seasonal-naive | Series won |
| :-- | --: | --: | --: | --: |
| Retrained, seed 42 | 61.68 | 57.54 | +7.3% | 72.1% |
| Retrained, seed 1 | 62.23 | 58.24 | +6.1% | 68.8% |
| **Retrained, seed 2 (served: best on validation)** | **56.94** | **52.62** | **+15.2%** | 67.5% |
| Retrained, mean of 3 | | 56.13 | **+9.5%** | 67 to 72% |
| Previous model, 2024 data only | | 58.49 | +5.7% | 60.7% |

Retraining on recent data helped: +9.5% on average against +5.7% for the same design trained on 2024 alone, and a
larger share of series won. The seed spread is wide (6.1 to 15.2%); the served seed was the best on validation and
also on test, so +15.2% is the luckiest draw, and +9.5% is the number to quote.

Interval coverage on test (nominal 0.80; by series size under 10 / 10-100 / 100-1,000 / over 1,000 events a day):

| Interval | Served seed | Overall | By size | Mean width |
| :-- | :-- | --: | :-- | --: |
| **Rolling 14 days (chosen on validation)** | seed 2 | 0.791 | 0.80 / 0.80 / 0.79 / 0.76 | 205 |
| Static, fitted on validation | seed 2 | 0.810 | 0.81 / 0.81 / 0.82 / 0.81 | 228 |

The choice made on validation was slightly the worse one on test: the rolling interval is 10% narrower but
under-covers the largest series (0.76), while the static one was even across sizes. Both are closer to 80% than on
the earlier periods (0.73 on the 2024 test, 0.77 on Q1 2025); a validation period right before the test period may
be part of the reason, but that was not tested. The rolling choice is kept as made; a second period would be needed
to tell the two apart.

The served checkpoint (`models/thp_gdelt.pt`, from `models/retrain_2025h1/seed2.pt`, written by `--stage serve`)
carries the rolling interval in its state at the end of the data (fitted on the windows observed in the last 14
days), and its test result, which the API reports as `baseline_comparison`.

## A stronger baseline: gradient boosting on the same split (2026-10-09)

Seasonal-naive is the baseline a daily count must beat, but not the strongest one available. A LightGBM model was
added on exactly the same cache, split and windows as the retrain above (`db_scripts/evaluate_strong_baseline.py`):

* **Features**, one row per window and horizon day: the 14 daily log counts, the last input day's other 15 features,
  the same-weekday log count a week before the target day, the horizon, the target's day of week, and the series'
  kind and measure as categories (optionally its id).
* **Chosen on validation only** (train 2024 + January 2025, 277,472 windows; validation February-March 2025, 39,008
  windows; early stopping on validation). Seven trials: four settle target and loss, three vary one setting from the
  best of those (`docs/forecast_eval/strong_baseline_select.json`):

| Target | Loss | Series id | Leaves | Rounds | Validation MAE |
| :-- | :-- | :-- | --: | --: | --: |
| residual on seasonal-naive (log) | L1 | yes | 63 | 440 | 57.50 |
| residual on seasonal-naive (log) | L2 | yes | 63 | 444 | 57.49 |
| log count | L1 | yes | 63 | 500 | 58.81 |
| log count | L2 | yes | 63 | 444 | 67.97 |
| **residual (log)** | **L2** | **no** | **63** | **1,728** | **54.33** |
| residual (log) | L2 | yes | 31 | 1,115 | 57.55 |
| residual (log) | L2 | yes | 127 | 243 | 56.86 |

  For reference on the same validation windows: seasonal-naive 67.19, the Transformer seeds 61.68, 62.23 and 56.94.
* **Tested once** with the chosen configuration (`docs/forecast_eval/strong_baseline_test.json`). The Transformer
  predictions were recomputed from the three checkpoints and reproduce the recorded test MAEs exactly.

| Model, test 2025-04-01 to 06-11 (48,576 windows) | Test MAE | vs seasonal-naive | Series won vs seasonal-naive |
| :-- | --: | --: | --: |
| Seasonal-naive | 62.05 | | |
| Transformer, seed 42 / 1 / 2 | 57.54 / 58.24 / 52.62 | +7.3% / +6.1% / +15.2% | 72.1% / 68.8% / 67.5% |
| Transformer, mean of 3 seeds | 56.13 | +9.5% | 67 to 72% |
| **LightGBM (chosen on validation)** | **53.52** | **+13.8%** | **77.9%** |

**LightGBM did better on this period.** Its MAE is 2.6 points (about 5%) below the Transformer's 3-seed average, it
wins on more series, and only the served seed 2, the luckiest of three draws, is lower; the interval below shows the
gap is not established.

How sure: MAE differences with a moving-block bootstrap over the 66 test start days (7-day blocks, 2,000 resamples,
95% percentile intervals; negative means the first model has lower error):

| Difference in MAE | Mean | 95% interval |
| :-- | --: | :-- |
| Transformer (3-seed mean) minus seasonal-naive | -5.92 | -8.75 to -4.12 |
| LightGBM minus seasonal-naive | -8.54 | -15.20 to -4.34 |
| Transformer (3-seed mean) minus LightGBM | +2.62 | -0.78 to +7.64 |
| Transformer seed 42 / 1 minus LightGBM | +4.03 / +4.73 | +0.59 to +9.64 / +1.30 to +10.01 |
| Transformer seed 2 (served) minus LightGBM | -0.90 | -4.52 to +3.06 |

Both models beat seasonal-naive on this period with intervals clear of zero. The Transformer-versus-LightGBM interval
includes zero for the seed average and for the served seed: on these 66 days the data do not establish that either
is better, while two of the three Transformer seeds are clearly worse than LightGBM. The point estimate favours
LightGBM, which is also simpler, trains in under a minute on a laptop CPU, and needs no GPU.

What the intervals cover and do not: variation over days like these (one 10-week period, resampled in weekly blocks
because neighbouring days share targets). They do not cover other periods, other training seeds (the Transformer
average is over three fixed seeds; for LightGBM see the seed check below), or the choice of features and trials.

**LightGBM's seed** (`docs/forecast_eval/strong_baseline_seeds.json`; a sensitivity check, nothing chosen on it). The
tested model is reproducible: refitting it with the same seed (2025) gives identical predictions. But it is not
seed-free, because the chosen configuration subsamples rows and features (bagging fraction 0.8, feature fraction
0.9), so the same configuration was refitted with seeds 42, 1 and 2, with the same early stopping on validation:

| LightGBM seed | Rounds | Validation MAE | Test MAE |
| :-- | --: | --: | --: |
| 2025 (the tested model) | 1,728 | 54.33 | 53.52 |
| 42 | 1,972 | 53.72 | 53.17 |
| 1 | 2,804 | 53.53 | 52.96 |
| 2 | 2,247 | 53.77 | 53.46 |
| **42 / 1 / 2, mean** | | **53.67** | **53.20** (range 52.96 to 53.46) |

LightGBM barely moves with the seed (0.5 MAE across three seeds), while the Transformer's three seeds span 52.62 to
58.24. Seed against seed, the Transformer's mean minus LightGBM's mean is +2.94 MAE, 95% interval **-0.42 to +7.74**
(same block bootstrap): still not established, with the point estimate favouring LightGBM.

**80% prediction intervals for LightGBM** (`docs/forecast_eval/strong_baseline_intervals.json`), by exactly the
Transformer's method and rule (`evaluate_retrain.py`): size-binned quantiles of log-space residuals; candidates a
static interval and rolling refits on the windows observed in the previous 14, 28 or 42 days; chosen on validation
(fitted on the first half, scored on the second) by the smallest worst-size-bin gap to 80%. The validation scores
pick **rolling 14 days**, the method the Transformer uses (worst-bin gaps: rolling 14 days 0.029, rolling 28 days
0.038, rolling 42 days 0.040, static 0.042). On test:

| 80% interval, test 2025-04-01 to 06-11 | Overall | <10 | 10-100 | 100-1,000 | 1,000+ | Mean width |
| :-- | --: | --: | --: | --: | --: | --: |
| **LightGBM, rolling 14 days (chosen)** | **0.786** | 0.798 | 0.785 | 0.785 | 0.750 | **194.3** |
| Transformer seeds 42 / 1 / 2, rolling 14 days (chosen) | 0.789 / 0.789 / 0.791 | 0.800 to 0.801 | 0.795 to 0.796 | 0.782 to 0.786 | 0.755 to 0.763 | 215.2 / 216.6 / 205.1 |
| LightGBM, static | 0.805 | 0.804 | 0.812 | 0.808 | 0.780 | 206.5 |
| Transformer seeds, static | 0.810 to 0.814 | 0.807 to 0.812 | 0.805 to 0.816 | 0.815 to 0.819 | 0.802 to 0.811 | 227.5 to 241.6 |

The intervals behave the same way for both models: just under 80% overall, lowest for the largest series, and the
static interval covers a little more but is wider. LightGBM's intervals are narrower at the same coverage (194 against
205 to 217 for the rolling interval), which follows from its smaller errors; they are not a better-calibrated method.

Limits of the comparison:

* The test period had already been used once, for the Transformer. LightGBM was chosen on validation only and then
  tested once, but its design (a residual on seasonal-naive) borrows what the Transformer work had learned.
* The grid was small (seven trials); neither model was tuned further for this comparison.
* The served model is still the Transformer (seed 2). Whether to serve LightGBM instead, or average both, is a
  decision for the next period, which has not been looked at.
* The seed check and the intervals used the test period again (for the third and fourth time in all); they were
  computed after the LightGBM choice and change nothing about it, and the interval method was chosen on validation.

## 2025 Q3: the decision (2026-10-09)

The Q2 comparison could not separate the Transformer from LightGBM, so the choice of served model was put to a period
neither had seen, by a rule committed and pushed before any of its data was downloaded
(`docs/forecast_eval/2025q3_decision_rule.md`, commit afa1791). Results: `docs/forecast_eval/2025q3_test.json`,
reproduce with `db_scripts/evaluate_2025q3.py`.

* **Data.** 2025-07-03 (the first full day after the GDELT outage) to 09-30, loaded into `events_2025` by the same
  rule as Q1 and Q2 (4,225,396 events; `docs/DATA_LAYER.md`). 70 forecast start days, 2025-07-17 to 09-24; 51,520
  windows of the same 736 series. The rolling input features look back up to 30 days, and the days before 07-03 are
  the outage, not zero events, so features were built only from data inside the period (the first windows' 30-day
  means use the days available). The same code path, run on Q2, reproduces the training cache's targets and
  non-rolling features exactly (38,272 windows).
* **Models, frozen.** The three Transformer checkpoints as they are; LightGBM with the configuration chosen on
  validation, refitted with the tested seed (2025), which reproduces the tested Q2 predictions exactly (max
  difference 0.0). No retraining, no tuning.

| Q3 2025 (51,520 windows) | MAE | vs seasonal-naive | Series won vs seasonal-naive | 80% coverage (rolling 14 days) | Mean width |
| :-- | --: | --: | --: | --: | --: |
| Seasonal-naive | 65.95 | | | | |
| Transformer seed 42 / 1 / 2 | 63.44 / 65.04 / 60.52 | +3.8% / +1.4% / +8.2% | 66.8% / 62.2% / 67.8% | 0.792 / 0.794 / 0.795 | 244.8 / 247.1 / 230.4 |
| Transformer, mean of 3 seeds | 63.00 | +4.5% | | 0.794 | |
| **LightGBM** | **57.21** | **+13.3%** | **77.4%** | **0.790** | **214.4** |

Coverage by series size (fewer than 10 / 10 to 100 / 100 to 1,000 / over 1,000 daily events), from 2025-07-31 (the
first 14 forecast days have no observed windows to refit on; 41,216 windows): LightGBM 0.798 / 0.796 / 0.782 /
0.776; Transformer seeds 0.799 to 0.801 / 0.798 to 0.800 / 0.789 to 0.792 / 0.752 to 0.786.

MAE differences, moving-block bootstrap over the 70 start days (7-day blocks, 2,000 resamples, 95%; negative means
the first model has lower error): Transformer (3-seed mean) minus LightGBM **+5.79, interval +1.68 to +9.38**;
Transformer minus seasonal-naive -2.95 (-9.34 to -0.35); LightGBM minus seasonal-naive -8.74 (-15.80 to -4.78);
seeds 42 / 1 / 2 minus LightGBM +6.23 (+2.80 to +10.11) / +7.83 (+2.68 to +11.61) / +3.32 (-1.32 to +7.42).

**The rule, applied:** (1) both models clear the coverage floor of 0.75 (Transformer 0.794 as the mean of its seeds,
LightGBM 0.790); (2) LightGBM has the lower MAE (57.21 against 63.00); (3) the interval of the difference excludes
0. **Decision: serve LightGBM.**

What the period showed beyond the decision: the Transformer's lead over seasonal-naive shrank from 9.5% on Q2 to 4.5%
here, while LightGBM's held (13.8% on Q2, 13.3% here); on Q3 LightGBM is better than every Transformer seed,
clearly so for seeds 42 and 1, and its intervals are narrower at about the same coverage.

Limits: one period of ten weeks, with a three-week outage before it, so the first windows' rolling features are
built from fewer days than in training; the models were trained on data up to January 2025 and are being tested
six to eight months later, which both face equally; per-day bootstrap intervals cover variation over days like
these, not other periods. The served model has not been switched yet; that is a separate change (the API and the
served checkpoint format are the Transformer's).

## Short answer for the models trained on 2024 only

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
> seasonal-naive baseline. Retrained on 2024 plus early 2025 and tested once on a later period (April to mid-June
> 2025, rebuilt from the official GDELT files), it cuts MAE by about 9.5% against seasonal-naive (3 seeds, 6 to 15%)
> and beats it on about 70% of the series; the same design trained on 2024 alone got 5.7% there. The Hawkes-style
> component of the original design hurt and was removed. The 80% intervals cover 0.79 overall on that period
> (0.76 for the largest series). A LightGBM baseline on the same split did better on that period (+13.8%, MAE
> 53.52 vs 56.13; 53.20 averaged over three seeds, which barely change it); the difference between the two is
> within the bootstrap interval (-0.78 to +7.64; seed means -0.42 to +7.74), so the Transformer is not shown to beat
> gradient boosting. LightGBM's 80% intervals, by the same method, cover 0.79 overall (0.75 for the largest series)
> and are about 8% narrower (mean width 194 against 212).

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

```bash
# The retrain on 2024 plus early 2025 (needs MySQL with events_2025 for the first step):
python db_scripts/load_gdelt_period.py --start 2025-04-01 --end 2025-06-30 --added-until 2025-07-07 --table events_2025
python db_scripts/build_extended_dataset.py --table events_2025 --end 2025-06-11 --series-from models/thp_gdelt.pt \
  --check-against models/thp_calibration_dataset_seq14_h7.npz --out models/thp_dataset_2024_2025h1_seq14_h7.npz
for seed in 42 1 2; do python db_scripts/train_thp_model.py \
  --dataset-cache models/thp_dataset_2024_2025h1_seq14_h7.npz --seq-len 14 --forecast-horizon 7 \
  --top-countries 3 --top-actors 50 --top-country-pairs 30 --top-actor-pairs 30 \
  --top-event-roots 20 --top-event-codes 50 --min-series-events 10 \
  --epochs 60 --batch-size 1024 --d-model 64 --layers 2 --heads 4 --lr 0.0007 --early-stopping-patience 8 \
  --seed $seed --target-mode seasonal_residual --hawkes-residual-weight 0 \
  --val-start-date 2025-02-01 --test-start-date 2025-04-01 --skip-test \
  --output models/retrain_2025h1/seed$seed.pt; done
python db_scripts/evaluate_retrain.py --stage select ...   # then --stage test, then --stage serve (see the script)
# LightGBM on the same split, and bootstrap intervals (stages: transformer, select, test, report; see the script):
python db_scripts/evaluate_strong_baseline.py --stage select --dataset-cache models/thp_dataset_2024_2025h1_seq14_h7.npz \
  --out docs/forecast_eval/strong_baseline_select.json
# Follow-ups: same-seed refit and validation/test predictions, seed check, 80% intervals (stages pool, seeds, intervals):
python db_scripts/evaluate_strong_baseline.py --stage pool --dataset-cache models/thp_dataset_2024_2025h1_seq14_h7.npz
python db_scripts/evaluate_strong_baseline.py --stage seeds --dataset-cache models/thp_dataset_2024_2025h1_seq14_h7.npz \
  --out docs/forecast_eval/strong_baseline_seeds.json
python db_scripts/evaluate_strong_baseline.py --stage intervals --dataset-cache models/thp_dataset_2024_2025h1_seq14_h7.npz \
  --out docs/forecast_eval/strong_baseline_intervals.json
# 2025 Q3 decision (rule: docs/forecast_eval/2025q3_decision_rule.md); load the period first:
python db_scripts/load_gdelt_period.py --start 2025-07-03 --end 2025-09-30 --added-until 2025-10-07 --table events_2025
python db_scripts/evaluate_2025q3.py --stage build   # then --stage transformer, --stage lightgbm, --stage report
```

## Next steps

1. Rolling and static intervals were close on the one test period and the validation choice was the slightly worse
   one; a second later period is needed to choose between them with any confidence.
2. Q2 2025 has now been used once. The next model change needs a later period, after the June 2025 GDELT outage.
3. The seed spread (6 to 15%) is large; an ensemble of seeds, chosen in advance, would be steadier than one seed.
4. Done: on 2025 Q3, by a rule fixed before the data was loaded, LightGBM was chosen to serve (see "2025 Q3: the
   decision"). Switching the served model is the remaining step.

## Served model

`models/thp_gdelt.pt` is `models/retrain_2025h1/seed2.pt` since 2026-10-09: trained on 2024 plus January 2025, chosen
on validation (February-March 2025), tested once on 2025-04-01 to 06-11 (MAE 52.62 vs 62.05 for seasonal-naive,
+15.2%; the 3-seed mean is +9.5%). Same 184 series and 4 event types as before. The backend adds the model output to
log1p(same weekday last week) from the input window, serves the size-binned log-space interval (rolling 14-day state
at the end of the data), and reports the test comparison as `baseline_comparison`. The application still serves 2024
data. The previous checkpoint (2024 only, seed 42) is in git history.
