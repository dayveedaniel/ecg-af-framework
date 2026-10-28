from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

from ecg_benchmark.models._utils import fit_threshold, label, load_json, numeric_value, save_json, sigmoid
from ecg_benchmark.models.base import FeatureRows, ModelInfo


@dataclass
class LakeMoormanCosenModel:
    """Short-RR entropy detector inspired by Lake and Moorman's COSEn work."""

    threshold: float = 0.35
    slope: float = 10.0

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="lake_moorman_cosen",
        description=(
            "Lightweight COSEn-inspired RR entropy detector. Uses available RR summary "
            "columns as a proxy for short-sequence entropy evidence."
        ),
        required_columns=["rr_cv", "rr_count", "rr_mean"],
        output_score="Logistic AF probability from a short-window RR entropy proxy.",
        reproducibility="Deterministic. Threshold can be fitted from training labels or supplied in config.",
        configurable_parameters={
            "threshold": "Entropy-proxy threshold for probability 0.5. Default: fitted when possible, else 0.35.",
            "slope": "Logistic slope around threshold. Default: 10.0.",
        },
    )

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "LakeMoormanCosenModel":
        self.slope = float(config.get("slope", self.slope))
        configured_threshold = config.get("threshold")
        if configured_threshold is not None:
            self.threshold = float(configured_threshold)
            return self
        values = [_entropy_proxy(row) for row in train_features]
        labels = [label(row) for row in train_features]
        self.threshold = fit_threshold(values, labels, default=self.threshold)
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        return [sigmoid(self.slope * (_entropy_proxy(row) - self.threshold)) for row in features]

    def save(self, s3_uri: str) -> None:
        save_json(s3_uri, {"model_id": self.info.model_id, "threshold": self.threshold, "slope": self.slope})

    @classmethod
    def load(cls, s3_uri: str) -> "LakeMoormanCosenModel":
        payload = load_json(s3_uri)
        return cls(threshold=float(payload["threshold"]), slope=float(payload["slope"]))


def _entropy_proxy(row: dict[str, Any]) -> float:
    rr_cv = max(0.0, numeric_value(row, "rr_cv"))
    rr_count = max(1.0, numeric_value(row, "rr_count", 1.0))
    rr_mean = max(1e-3, numeric_value(row, "rr_mean", 1.0))
    return math.log1p(rr_cv * rr_count) / math.sqrt(rr_mean)
