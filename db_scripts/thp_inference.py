"""
Batch inference with a saved forecaster checkpoint, exactly as training evaluates it
(normalisation, series ids and the seasonal-residual offset all come from the checkpoint).
Used to evaluate a frozen model on new windows (2025) and to fit intervals on validation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services.thp_neural import NeuralTransformerHawkesModel, series_group_key  # noqa: E402


class Checkpoint:
    def __init__(self, path: str | Path):
        self.raw = torch.load(path, map_location="cpu", weights_only=False)
        self.model = NeuralTransformerHawkesModel(**self.raw["config"]).eval()
        self.model.load_state_dict(self.raw["model_state"])
        self.mean = np.asarray(self.raw["feature_mean"], dtype=np.float32)
        self.std = np.asarray(self.raw["feature_std"], dtype=np.float32)
        self.target_mode = self.raw.get("target_mode", "log")
        self.series_to_id: Dict[str, int] = self.raw["series_to_id"]
        self.event_type_to_id: Dict[str, int] = self.raw["event_type_to_id"]
        self.group_to_id: Dict[str, int] = self.raw["series_group_to_id"]
        self.seq_len = int(self.raw["config"]["seq_len"])

    def ids(self, labels: Sequence[Tuple[str, str]]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        s = np.asarray([self.series_to_id[sid] for sid, _ in labels], dtype=np.int64)
        e = np.asarray([self.event_type_to_id[et] for _, et in labels], dtype=np.int64)
        g = np.asarray([self.group_to_id[series_group_key(sid)] for sid, _ in labels], dtype=np.int64)
        return s, e, g

    @torch.no_grad()
    def predict(self, x_raw: np.ndarray, labels: List[Tuple[str, str]], horizon: int, batch: int = 4096) -> np.ndarray:
        """Expected counts, shape (n, horizon). ``x_raw`` is make_dataset's feature tensor."""
        s, e, g = self.ids(labels)
        x = (x_raw - self.mean) / self.std
        offset = np.zeros((len(x_raw), horizon), dtype=np.float32)
        if self.target_mode == "seasonal_residual":
            last_week = x_raw[:, -7:, 0]  # feature 0 = log1p(count)
            offset = last_week[:, [h % 7 for h in range(horizon)]]
        h = torch.arange(1, horizon + 1, dtype=torch.float32)
        out = []
        for i in range(0, len(x), batch):
            pred, _ = self.model(torch.tensor(x[i:i + batch], dtype=torch.float32), h,
                                 torch.tensor(s[i:i + batch]), torch.tensor(e[i:i + batch]), torch.tensor(g[i:i + batch]))
            log = pred.numpy() * self.raw["target_std"] + self.raw["target_mean"] + offset[i:i + batch]
            out.append(np.maximum(0.0, np.expm1(log)))
        return np.vstack(out).astype(np.float32)
