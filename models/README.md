# Model Artifacts

The served Transformer forecast model and the training/evaluation artifacts behind it.

Key files:

- `thp_gdelt.pt`: served checkpoint, loaded by the Forecast page and forecast API. Since 2026-10-09 it is
  `retrain_2025h1/seed2.pt` (trained on 2024 plus January 2025), with its tested intervals and test result.
- `retrain_2025h1/`: the three seeds of that retrain; `thp_dataset_2024_2025h1_seq14_h7.npz`: their training data.
- `thp_training_dataset.npz`, `thp_calibration_dataset_seq14_h7.npz`: 2024-only arrays of the earlier models.
- `training_logs/`: JSONL/CSV-style training logs and run metadata.
- `thp_sweeps/`: hyperparameter sweep outputs and intermediate checkpoints.

How they were evaluated: `docs/FORECAST_EVALUATION.md`. The model is loaded through `backend/services/thp_neural.py`
and exposed through the FastAPI data forecast route.
