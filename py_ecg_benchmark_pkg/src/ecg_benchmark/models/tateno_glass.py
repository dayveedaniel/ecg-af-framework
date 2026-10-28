from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from ecg_benchmark.models._utils import fit_threshold, label, load_json, numeric_value, save_json, sigmoid
from ecg_benchmark.models.base import FeatureRows, ModelInfo


@dataclass
class TatenoGlassRRHistogramModel:
    """RR coefficient-of-variation detector inspired by Tateno and Glass."""

    threshold: float = 0.18
    slope: float = 18.0

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="tateno_glass_rr_histogram",
        description=(
            "Paper-inspired RR detector based on coefficient-of-variation evidence from "
            "RR and delta-RR histogram AF work by Tateno and Glass."
        ),
        required_columns=["rr_cv", "rr_mean", "rr_std"],
        output_score="Logistic AF probability from RR coefficient-of-variation evidence.",
        reproducibility="Deterministic. Threshold can be fitted from training labels or supplied in config.",
        configurable_parameters={
            "threshold": "RR CV threshold for probability 0.5. Default: fitted when labels have both classes, else 0.18.",
            "slope": "Logistic slope around threshold. Default: 18.0.",
        },
    )

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "TatenoGlassRRHistogramModel":
        self.slope = float(config.get("slope", self.slope))
        configured_threshold = config.get("threshold")
        if configured_threshold is not None:
            self.threshold = float(configured_threshold)
            return self
        values = [_rr_cv(row) for row in train_features]
        labels = [label(row) for row in train_features]
        self.threshold = fit_threshold(values, labels, default=self.threshold)
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        return [sigmoid(self.slope * (_rr_cv(row) - self.threshold)) for row in features]

    def save(self, s3_uri: str) -> None:
        save_json(s3_uri, {"model_id": self.info.model_id, "threshold": self.threshold, "slope": self.slope})

    @classmethod
    def load(cls, s3_uri: str) -> "TatenoGlassRRHistogramModel":
        payload = load_json(s3_uri)
        return cls(threshold=float(payload["threshold"]), slope=float(payload["slope"]))


def _rr_cv(row: dict[str, Any]) -> float:
    rr_cv = numeric_value(row, "rr_cv")
    if rr_cv > 0:
        return rr_cv
    rr_mean = numeric_value(row, "rr_mean")
    rr_std = numeric_value(row, "rr_std")
    return rr_std / rr_mean if rr_mean > 0 else 0.0
