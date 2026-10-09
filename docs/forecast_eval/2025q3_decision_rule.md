# Which forecaster to serve: the rule, fixed before the test period is loaded

Written 2026-10-09, before any GDELT data after 2025-07-02 was downloaded or loaded. Committed and pushed on
its own, so the commit time shows the rule came first. The comparison on 2025-04-01..06-11
(`../FORECAST_EVALUATION.md`, "A stronger baseline") could not separate the two models; this rule decides on a
period neither has seen.

## Test period

* Events from 2025-07-03 (the first full day after the GDELT outage that ended 2025-07-02 02:00) to 2025-09-30,
  loaded with `db_scripts/load_gdelt_period.py` by the same rule as Q1 and Q2 (batches read until 2025-10-07 for
  late additions), into `events_2025`.
* Windows: 14 days of input and 7 days of targets, all inside the period; the same 736 series. Nothing in this
  period is used for training, tuning or choosing anything other than this one decision.

## Models (frozen, no retraining, no tuning)

* Transformer: `models/retrain_2025h1/seed42.pt`, `seed1.pt`, `seed2.pt` (trained on 2024 plus January 2025).
* LightGBM: the configuration chosen on validation (`strong_baseline_select.json`), refitted on the same training
  data with the tested seed (2025), which reproduces the tested model exactly.
* Baseline: seasonal-naive (the same weekday a week earlier).
* 80% intervals: the rolling 14-day method both models chose on validation, refitted only on windows whose targets
  are observed before the forecast day. The first 14 days of windows have no such history and are excluded from
  the coverage figures (not from MAE).

## Measures

* Count-space MAE over all windows and series. The Transformer's figure is the mean of its three seeds (the served
  seed 2 is reported as well).
* 95% interval of the MAE difference, Transformer (3-seed mean) minus LightGBM: moving-block bootstrap over
  forecast start days, 7-day blocks, 2,000 resamples (`thp_eval_utils.moving_block_bootstrap_ci`).
* Coverage of the 80% interval, overall and by series size.

## Decision

1. A model whose overall 80% interval coverage is below 0.75 cannot be served.
2. Of the models left, the one with the lower MAE is served.
3. If the 95% interval of the difference includes 0, LightGBM is served: it is the simpler model, it is stable
   across seeds (52.96 to 53.46 on Q2), and the Transformer would not have shown it is better.
4. If the Transformer is chosen, the served checkpoint stays seed 2.

Whatever the result, it is reported as it comes out, including a result that favours neither model by much.
