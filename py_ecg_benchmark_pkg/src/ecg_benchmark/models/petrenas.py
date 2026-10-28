from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from ecg_benchmark.models._utils import fit_threshold, label, load_json, numeric_value, save_json, sigmoid
from ecg_benchmark.models.base import FeatureRows, ModelInfo


@dataclass
class PetrenasLowComplexityModel:
    """Low-complexity RR detector inspired by Petrenas, Marozas, and Sornmo."""

    irregularity_threshold: float = 0.12
    rate_threshold_bpm: float = 80.0
    slope: float = 14.0
    rate_weight: float = 0.25

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="petrenas_low_complexity_detector",
        description=(
            "Paper-inspired low-complexity detector that fuses RR irregularity and heart-rate "
            "evidence for brief AF-window screening."
        ),
        required_columns=["rr_cv", "rr_mean", "rr_count"],
        output_score="Fused AF probability from RR irregularity and heart-rate evidence.",
        reproducibility="Deterministic. Irregularity threshold can be fitted from training labels or supplied.",
        configurable_parameters={
            "irregularity_threshold": "RR CV threshold for irregularity evidence. Default: fitted when possible, else 0.12.",
            "rate_threshold_bpm": "Heart-rate threshold for rate evidence. Default: 80.",
            "slope": "Logistic slope for both evidence channels. Default: 14.0.",
            "rate_weight": "Weight assigned to heart-rate evidence. Default: 0.25.",
        },
    )

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "PetrenasLowComplexityModel":
        self.rate_threshold_bpm = float(config.get("rate_threshold_bpm", self.rate_threshold_bpm))
        self.slope = float(config.get("slope", self.slope))
        self.rate_weight = float(config.get("rate_weight", self.rate_weight))
        configured_threshold = config.get("irregularity_threshold")
        if configured_threshold is not None:
            self.irregularity_threshold = float(configured_threshold)
            return self
        values = [numeric_value(row, "rr_cv") for row in train_features]
        labels = [label(row) for row in train_features]
        self.irregularity_threshold = fit_threshold(values, labels, default=self.irregularity_threshold)
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        scores: list[float] = []
        weight = min(1.0, max(0.0, self.rate_weight))
        for row in features:
            irregularity = numeric_value(row, "rr_cv")
            rr_mean = numeric_value(row, "rr_mean")
            bpm = 60.0 / rr_mean if rr_mean > 0 else 0.0
            irregularity_score = sigmoid(self.slope * (irregularity - self.irregularity_threshold))
            rate_score = sigmoid((self.slope / 10.0) * (bpm - self.rate_threshold_bpm))
            scores.append(((1.0 - weight) * irregularity_score) + (weight * rate_score))
        return scores

    def save(self, s3_uri: str) -> None:
        save_json(
            s3_uri,
            {
                "model_id": self.info.model_id,
                "irregularity_threshold": self.irregularity_threshold,
                "rate_threshold_bpm": self.rate_threshold_bpm,
                "slope": self.slope,
                "rate_weight": self.rate_weight,
            },
        )

    @classmethod
    def load(cls, s3_uri: str) -> "PetrenasLowComplexityModel":
        payload = load_json(s3_uri)
        return cls(
            irregularity_threshold=float(payload["irregularity_threshold"]),
            rate_threshold_bpm=float(payload["rate_threshold_bpm"]),
            slope=float(payload["slope"]),
            rate_weight=float(payload["rate_weight"]),
        )
