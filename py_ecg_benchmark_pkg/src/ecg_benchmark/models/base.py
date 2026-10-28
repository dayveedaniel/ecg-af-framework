from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


FeatureRows = list[dict[str, Any]]


@dataclass(frozen=True)
class ModelInfo:
    """Documentation metadata declared by every benchmark model."""

    model_id: str
    description: str
    required_columns: list[str]
    output_score: str
    reproducibility: str
    configurable_parameters: dict[str, str]
    input_kind: str = "tabular"
    requires_training: bool = True


@runtime_checkable
class BenchmarkModel(Protocol):
    """Model extension API for AF detection benchmark models.

    Implementations may wrap sklearn, Spark ML, a simple rule, or a future deep
    learning model. ``predict_proba`` must return AF probabilities, where higher
    scores mean stronger evidence for atrial fibrillation.
    """

    info: ModelInfo

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "BenchmarkModel":
        """Fit model state from train and validation feature rows."""

    def predict_proba(self, features: FeatureRows) -> list[float]:
        """Return one AF probability per feature row."""

    def save(self, s3_uri: str) -> None:
        """Persist the model artifact under an S3 URI."""

    @classmethod
    def load(cls, s3_uri: str) -> "BenchmarkModel":
        """Restore a model artifact from an S3 URI."""


class ModelNotFittedError(RuntimeError):
    """Raised when predictions are requested before fitting or loading."""
