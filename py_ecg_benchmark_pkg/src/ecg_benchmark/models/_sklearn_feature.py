from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._utils import class_prior, label, load_pickle, matrix, resolve_feature_columns, save_pickle
from ecg_benchmark.models.base import FeatureRows, ModelInfo, ModelNotFittedError


class SklearnShowcaseModel:
    """Shared wrapper for paper-inspired tabular sklearn showcase models."""

    info: ClassVar[ModelInfo]
    default_params: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        estimator: Any | None = None,
        feature_columns: list[str] | None = None,
        constant_probability: float | None = None,
    ) -> None:
        self.estimator = estimator
        self.feature_columns = feature_columns or []
        self.constant_probability = constant_probability

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "SklearnShowcaseModel":
        params = dict(self.default_params)
        params.update(config)
        configured_feature_columns = params.pop("feature_columns", None)
        self.feature_columns = resolve_feature_columns(train_features, configured_feature_columns)
        y_train = [label(row) for row in train_features]
        if len(set(y_train)) < 2:
            self.constant_probability = class_prior(train_features)
            self.estimator = None
            return self
        x_train = matrix(train_features, self.feature_columns)
        self.estimator = self._build_estimator(params)
        self.estimator.fit(x_train, y_train)
        self.constant_probability = None
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        if self.constant_probability is not None:
            return [float(self.constant_probability)] * len(features)
        if self.estimator is None:
            raise ModelNotFittedError(f"{self.info.model_id} has not been fitted")
        x_rows = matrix(features, self.feature_columns)
        probabilities = self.estimator.predict_proba(x_rows)
        classes = list(self.estimator.classes_)
        positive_idx = classes.index(1) if 1 in classes else len(classes) - 1
        return [float(row[positive_idx]) for row in probabilities]

    def save(self, s3_uri: str) -> None:
        if self.estimator is None and self.constant_probability is None:
            raise ModelNotFittedError(f"{self.info.model_id} has not been fitted")
        save_pickle(
            s3_uri,
            {
                "model_id": self.info.model_id,
                "feature_columns": self.feature_columns,
                "estimator": self.estimator,
                "constant_probability": self.constant_probability,
            },
        )

    @classmethod
    def load(cls, s3_uri: str) -> "SklearnShowcaseModel":
        payload = load_pickle(s3_uri)
        return cls(
            estimator=payload.get("estimator"),
            feature_columns=list(payload.get("feature_columns") or []),
            constant_probability=payload.get("constant_probability"),
        )

    def _build_estimator(self, params: dict[str, Any]):
        raise NotImplementedError
