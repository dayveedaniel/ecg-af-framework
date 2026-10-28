from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._sklearn_feature import SklearnShowcaseModel
from ecg_benchmark.models.base import ModelInfo


class GoodfellowGradientBoostingModel(SklearnShowcaseModel):
    """Gradient-boosted feature model inspired by Goodfellow et al."""

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="goodfellow_xgboost_multifeature",
        description=(
            "Paper-inspired gradient-boosted feature classifier over waveform, template, "
            "RRI, nonlinear, and morphology-style features. Uses XGBoost when installed, "
            "otherwise sklearn GradientBoostingClassifier."
        ),
        required_columns=["label", "feature columns"],
        output_score="Boosted-tree AF class probability.",
        reproducibility="Deterministic when random_state is set. Falls back to training prevalence for single-class training data.",
        configurable_parameters={
            "feature_columns": "Optional explicit list of feature columns. Default: all numeric non-identity columns.",
            "backend": "xgboost or sklearn. Default: sklearn.",
            "n_estimators": "Number of boosting rounds. Default: 150.",
            "learning_rate": "Boosting learning rate. Default: 0.05.",
            "max_depth": "Tree max depth. Default: 3.",
            "random_state": "Integer seed. Default: benchmark random_seed.",
        },
    )
    default_params: ClassVar[dict[str, Any]] = {
        "backend": "sklearn",
        "n_estimators": 150,
        "learning_rate": 0.05,
        "max_depth": 3,
        "random_state": None,
    }

    def _build_estimator(self, params: dict[str, Any]):
        backend = str(params.pop("backend", "sklearn")).lower()
        random_state = params.get("random_state")
        if random_state is None:
            params.pop("random_state", None)
        if backend == "xgboost":
            try:
                from xgboost import XGBClassifier
            except ImportError as exc:
                raise RuntimeError("xgboost backend requested but xgboost is not installed") from exc
            return XGBClassifier(
                objective="binary:logistic",
                eval_metric="logloss",
                tree_method="hist",
                **params,
            )
        try:
            from sklearn.ensemble import GradientBoostingClassifier
        except ImportError as exc:
            raise RuntimeError("scikit-learn is required for goodfellow_xgboost_multifeature") from exc
        return GradientBoostingClassifier(**params)
