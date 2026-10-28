from __future__ import annotations

from typing import Any, ClassVar

from ecg_benchmark.models._sklearn_feature import SklearnShowcaseModel
from ecg_benchmark.models.base import ModelInfo


class BinAdaBoostTreeModel(SklearnShowcaseModel):
    """Compact decision-tree ensemble inspired by Guangyu Bin et al."""

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="bin_adaboost_tree_ensemble",
        description=(
            "Paper-inspired AdaBoost decision-tree ensemble over compact ECG/RR feature sets, "
            "based on the CinC 2017 decision-tree ensemble entry."
        ),
        required_columns=["label", "feature columns"],
        output_score="AdaBoost AF class probability.",
        reproducibility="Deterministic when random_state is set. Falls back to training prevalence for single-class training data.",
        configurable_parameters={
            "feature_columns": "Optional explicit list of feature columns. Default: all numeric non-identity columns.",
            "n_estimators": "Number of boosting rounds. Default: 80.",
            "learning_rate": "Boosting learning rate. Default: 0.5.",
            "max_depth": "Weak tree max depth. Default: 2.",
            "random_state": "Integer seed. Default: benchmark random_seed.",
        },
    )
    default_params: ClassVar[dict[str, Any]] = {
        "n_estimators": 80,
        "learning_rate": 0.5,
        "max_depth": 2,
        "random_state": None,
    }

    def _build_estimator(self, params: dict[str, Any]):
        try:
            from sklearn.ensemble import AdaBoostClassifier
            from sklearn.tree import DecisionTreeClassifier
        except ImportError as exc:
            raise RuntimeError("scikit-learn is required for bin_adaboost_tree_ensemble") from exc
        max_depth = int(params.pop("max_depth", 2))
        random_state = params.get("random_state")
        if random_state is None:
            params.pop("random_state", None)
        tree = DecisionTreeClassifier(max_depth=max_depth, random_state=random_state)
        try:
            return AdaBoostClassifier(estimator=tree, **params)
        except TypeError:
            return AdaBoostClassifier(base_estimator=tree, **params)
