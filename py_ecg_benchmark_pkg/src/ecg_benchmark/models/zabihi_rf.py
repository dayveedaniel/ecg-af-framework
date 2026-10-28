from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._sklearn_feature import SklearnShowcaseModel
from ecg_benchmark.models.base import ModelInfo


class ZabihiRandomForestModel(SklearnShowcaseModel):
    """Random-forest feature model inspired by the tied 1st-place CinC 2017 entry."""

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="zabihi_rf_handcrafted",
        description=(
            "Paper-inspired random forest over handcrafted ECG/RR features, based on the "
            "Zabihi et al. PhysioNet/CinC 2017 hand-held ECG classifier."
        ),
        required_columns=["label", "feature columns"],
        output_score="Random-forest AF class probability.",
        reproducibility="Deterministic when random_state is set. Falls back to training prevalence for single-class training data.",
        configurable_parameters={
            "feature_columns": "Optional explicit list of feature columns. Default: all numeric non-identity columns.",
            "n_estimators": "Number of trees. Default: 250.",
            "max_depth": "Maximum tree depth. Default: None.",
            "max_features": "sklearn max_features. Default: sqrt.",
            "class_weight": "sklearn class_weight. Default: balanced.",
            "random_state": "Integer seed. Default: benchmark random_seed.",
        },
    )
    default_params: ClassVar[dict[str, Any]] = {
        "n_estimators": 250,
        "max_depth": None,
        "max_features": "sqrt",
        "class_weight": "balanced",
        "random_state": None,
        "n_jobs": -1,
    }

    def _build_estimator(self, params: dict[str, Any]):
        try:
            from sklearn.ensemble import RandomForestClassifier
        except ImportError as exc:
            raise RuntimeError("scikit-learn is required for zabihi_rf_handcrafted") from exc
        if params.get("random_state") is None:
            params.pop("random_state", None)
        return RandomForestClassifier(**params)
