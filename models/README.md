# Model Artifacts

The served forecast model and the training/evaluation artifacts behind it.

Key files:

- `lightgbm_gdelt.json`: the served model since 2026-10-09 (LightGBM, chosen by the 2025 Q3 rule in
  `docs/forecast_eval/2025q3_decision_rule.md`), with its intervals and test results; loaded by
  `backend/services/lgbm_forecaster.py`. Written by `db_scripts/evaluate_strong_baseline.py --stage serve`.
- `thp_gdelt.pt`: the Transformer checkpoint (`retrain_2025h1/seed2.pt`, trained on 2024 plus January 2025), served
  until then; set `FORECAST_MODEL_PATH=models/thp_gdelt.pt` to serve it again.
- `retrain_2025h1/`: the three seeds of that retrain; `thp_dataset_2024_2025h1_seq14_h7.npz`: their training data.
- `thp_training_dataset.npz`, `thp_calibration_dataset_seq14_h7.npz`: 2024-only arrays of the earlier models.
- `training_logs/`: JSONL/CSV-style training logs and run metadata.
- `thp_sweeps/`: hyperparameter sweep outputs and intermediate checkpoints.

How they were evaluated: `docs/FORECAST_EVALUATION.md`. The service (`backend/services/thp_service.py`) loads either
file and exposes it through the FastAPI data forecast route.
