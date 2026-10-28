from __future__ import annotations

import json
import math
import pickle
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar

from ecg_benchmark.artifacts import parse_s3_uri
from ecg_benchmark.models.bin_adaboost import BinAdaBoostTreeModel
from ecg_benchmark.models.cpsc2021_resnet_transformer import Cpsc2021ResNetTransformerModel
from ecg_benchmark.models.goodfellow_xgboost import GoodfellowGradientBoostingModel
from ecg_benchmark.models.lake_moorman import LakeMoormanCosenModel
from ecg_benchmark.models.petrenas import PetrenasLowComplexityModel
from ecg_benchmark.models.base import BenchmarkModel, FeatureRows, ModelInfo, ModelNotFittedError
from ecg_benchmark.models.raw_cnn import RawCnn1DModel
from ecg_benchmark.models.tateno_glass import TatenoGlassRRHistogramModel
from ecg_benchmark.models.xiong_skip_cnn import XiongSkipCnn1DModel
from ecg_benchmark.models.zabihi_rf import ZabihiRandomForestModel
from ecg_benchmark.models.zihlmann_crnn import ZihlmannCrnnSpectrogramModel
from ecg_benchmark.storage import S3ArtifactStore


@dataclass
class DummyPriorModel:
    """Predict the AF prevalence observed in the training split."""

    prior: float | None = None

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="dummy_prior",
        description="Constant baseline that predicts the training-set AF prevalence for every window.",
        required_columns=["label"],
        output_score="Probability of AF; identical for every row after fitting.",
        reproducibility="Deterministic given the training labels.",
        configurable_parameters={},
    )

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "DummyPriorModel":
        labels = [_label(row) for row in train_features]
        self.prior = (sum(labels) / len(labels)) if labels else 0.0
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        if self.prior is None:
            raise ModelNotFittedError("dummy_prior has not been fitted")
        return [float(self.prior)] * len(features)

    def save(self, s3_uri: str) -> None:
        _save_json(s3_uri, {"model_id": self.info.model_id, "prior": self.prior})

    @classmethod
    def load(cls, s3_uri: str) -> "DummyPriorModel":
        payload = _load_json(s3_uri)
        return cls(prior=float(payload["prior"]))


@dataclass
class RRIrregularityRuleModel:
    """Rule baseline that maps RR-interval irregularity to an AF score."""

    threshold: float = 0.12
    slope: float = 12.0

    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="rr_irregularity_rule",
        description="No-fit rule that scores windows by RR interval coefficient of variation.",
        required_columns=["rr_cv"],
        output_score="Logistic transform of rr_cv around the configured threshold; higher means more irregular.",
        reproducibility="Deterministic. No learned state beyond configured threshold and slope.",
        configurable_parameters={
            "threshold": "RR coefficient-of-variation value corresponding to probability 0.5. Default: 0.12.",
            "slope": "Logistic slope around threshold. Default: 12.0.",
        },
        requires_training=False,
    )

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "RRIrregularityRuleModel":
        self.threshold = float(config.get("threshold", self.threshold))
        self.slope = float(config.get("slope", self.slope))
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        scores: list[float] = []
        for row in features:
            rr_cv = float(row.get("rr_cv", 0.0) or 0.0)
            scores.append(1.0 / (1.0 + math.exp(-self.slope * (rr_cv - self.threshold))))
        return scores

    def save(self, s3_uri: str) -> None:
        _save_json(
            s3_uri,
            {"model_id": self.info.model_id, "threshold": self.threshold, "slope": self.slope},
        )

    @classmethod
    def load(cls, s3_uri: str) -> "RRIrregularityRuleModel":
        payload = _load_json(s3_uri)
        return cls(threshold=float(payload["threshold"]), slope=float(payload["slope"]))


class SklearnFeatureModel:
    """Shared sklearn wrapper for tabular feature baselines."""

    info: ClassVar[ModelInfo]
    estimator_name: ClassVar[str]
    default_params: ClassVar[dict[str, Any]]

    def __init__(self, estimator: Any | None = None, feature_columns: list[str] | None = None) -> None:
        self.estimator = estimator
        self.feature_columns = feature_columns or []

    def fit(self, train_features: FeatureRows, val_features: FeatureRows, config: dict[str, Any]) -> "SklearnFeatureModel":
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.linear_model import LogisticRegression
        except ImportError as exc:
            raise RuntimeError("scikit-learn is required for feature baseline models") from exc

        params = dict(self.default_params)
        params.update(config)
        configured_feature_columns = params.pop("feature_columns", None)
        if "random_state" in params and params["random_state"] is None:
            params.pop("random_state")
        self.feature_columns = _resolve_feature_columns(train_features, configured_feature_columns)
        x_train = _matrix(train_features, self.feature_columns)
        y_train = [_label(row) for row in train_features]
        if self.estimator_name == "logistic_regression":
            self.estimator = LogisticRegression(**params)
        elif self.estimator_name == "random_forest":
            self.estimator = RandomForestClassifier(**params)
        else:
            raise ValueError(f"Unknown sklearn estimator: {self.estimator_name}")
        self.estimator.fit(x_train, y_train)
        return self

    def predict_proba(self, features: FeatureRows) -> list[float]:
        if self.estimator is None:
            raise ModelNotFittedError(f"{self.info.model_id} has not been fitted")
        x_rows = _matrix(features, self.feature_columns)
        probabilities = self.estimator.predict_proba(x_rows)
        classes = list(self.estimator.classes_)
        positive_idx = classes.index(1) if 1 in classes else len(classes) - 1
        return [float(row[positive_idx]) for row in probabilities]

    def save(self, s3_uri: str) -> None:
        if self.estimator is None:
            raise ModelNotFittedError(f"{self.info.model_id} has not been fitted")
        _save_pickle(
            s3_uri,
            {
                "model_id": self.info.model_id,
                "feature_columns": self.feature_columns,
                "estimator": self.estimator,
            },
        )

    @classmethod
    def load(cls, s3_uri: str) -> "SklearnFeatureModel":
        payload = _load_pickle(s3_uri)
        return cls(estimator=payload["estimator"], feature_columns=list(payload["feature_columns"]))


class LogisticRegressionFeaturesModel(SklearnFeatureModel):
    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="logistic_regression_features",
        description="Logistic regression over Spark-generated tabular ECG features.",
        required_columns=["label", "feature columns"],
        output_score="Calibrated-ish sklearn class probability for AF.",
        reproducibility="Deterministic when random_state is set. Defaults use the benchmark random_seed.",
        configurable_parameters={
            "feature_columns": "Optional explicit list of feature columns. Default: all numeric non-identity columns.",
            "max_iter": "Maximum optimization iterations. Default: 1000.",
            "class_weight": "sklearn class_weight. Default: balanced.",
            "random_state": "Integer seed. Default: benchmark random_seed.",
        },
    )
    estimator_name: ClassVar[str] = "logistic_regression"
    default_params: ClassVar[dict[str, Any]] = {"max_iter": 1000, "class_weight": "balanced", "random_state": None}


class RandomForestFeaturesModel(SklearnFeatureModel):
    info: ClassVar[ModelInfo] = ModelInfo(
        model_id="random_forest_features",
        description="Random forest over Spark-generated tabular ECG features.",
        required_columns=["label", "feature columns"],
        output_score="sklearn class probability for AF.",
        reproducibility="Deterministic when random_state is set. Defaults use the benchmark random_seed.",
        configurable_parameters={
            "feature_columns": "Optional explicit list of feature columns. Default: all numeric non-identity columns.",
            "n_estimators": "Number of trees. Default: 200.",
            "max_depth": "Maximum tree depth. Default: None.",
            "class_weight": "sklearn class_weight. Default: balanced.",
            "random_state": "Integer seed. Default: benchmark random_seed.",
        },
    )
    estimator_name: ClassVar[str] = "random_forest"
    default_params: ClassVar[dict[str, Any]] = {
        "n_estimators": 200,
        "max_depth": None,
        "class_weight": "balanced",
        "random_state": None,
    }


def create_model(model_id: str) -> BenchmarkModel:
    registry = model_class_registry()
    try:
        return registry[model_id]()
    except KeyError as exc:
        raise KeyError(f"Unknown model id '{model_id}'. Registered models: {sorted(registry)}") from exc


def load_model(model_id: str, s3_uri: str) -> BenchmarkModel:
    registry = model_class_registry()
    try:
        model_cls = registry[model_id]
    except KeyError as exc:
        raise KeyError(f"Unknown model id '{model_id}'. Registered models: {sorted(registry)}") from exc
    return model_cls.load(s3_uri)


def model_class_registry() -> dict[str, type[BenchmarkModel]]:
    return {
        DummyPriorModel.info.model_id: DummyPriorModel,
        RRIrregularityRuleModel.info.model_id: RRIrregularityRuleModel,
        LogisticRegressionFeaturesModel.info.model_id: LogisticRegressionFeaturesModel,
        RandomForestFeaturesModel.info.model_id: RandomForestFeaturesModel,
        RawCnn1DModel.info.model_id: RawCnn1DModel,
        TatenoGlassRRHistogramModel.info.model_id: TatenoGlassRRHistogramModel,
        LakeMoormanCosenModel.info.model_id: LakeMoormanCosenModel,
        PetrenasLowComplexityModel.info.model_id: PetrenasLowComplexityModel,
        ZabihiRandomForestModel.info.model_id: ZabihiRandomForestModel,
        BinAdaBoostTreeModel.info.model_id: BinAdaBoostTreeModel,
        GoodfellowGradientBoostingModel.info.model_id: GoodfellowGradientBoostingModel,
        XiongSkipCnn1DModel.info.model_id: XiongSkipCnn1DModel,
        ZihlmannCrnnSpectrogramModel.info.model_id: ZihlmannCrnnSpectrogramModel,
        Cpsc2021ResNetTransformerModel.info.model_id: Cpsc2021ResNetTransformerModel,
    }


def model_infos() -> list[ModelInfo]:
    return [model_cls.info for model_cls in model_class_registry().values()]


def _label(row: dict[str, Any]) -> int:
    if "label" not in row:
        raise KeyError("Feature rows must contain a 'label' column")
    label = int(row["label"])
    if label not in {0, 1}:
        raise ValueError(f"Labels must be 0 or 1, got: {label}")
    return label


def _resolve_feature_columns(rows: FeatureRows, configured: Any) -> list[str]:
    if configured:
        if not isinstance(configured, list):
            raise ValueError("feature_columns must be a list when provided")
        return [str(item) for item in configured]
    excluded = {
        "label",
        "dataset_name",
        "recording_id",
        "full_recording_id",
        "window_index",
        "window_start_seconds",
        "window_end_seconds",
        "start_sample",
        "end_sample",
        "split",
        "subject_id",
    }
    columns: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key in excluded or key in columns:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                columns.append(key)
    if not columns:
        raise ValueError("No numeric feature columns found")
    return sorted(columns)


def _matrix(rows: FeatureRows, columns: list[str]) -> list[list[float]]:
    matrix: list[list[float]] = []
    for row in rows:
        matrix.append([float(row.get(column, 0.0) or 0.0) for column in columns])
    return matrix


def _save_json(s3_uri: str, payload: dict[str, Any]) -> None:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.json"))
    store.put_json(parsed.bucket, parsed.key, payload)


def _load_json(s3_uri: str) -> dict[str, Any]:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.json"))
    return json.loads(store.get_bytes(parsed.bucket, parsed.key).decode("utf-8"))


def _save_pickle(s3_uri: str, payload: dict[str, Any]) -> None:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.pkl"))
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "model.pkl"
        path.write_bytes(pickle.dumps(payload))
        store.upload_file(parsed.bucket, parsed.key, path)


def _load_pickle(s3_uri: str) -> dict[str, Any]:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(_artifact_key_uri(s3_uri, "model.pkl"))
    return pickle.loads(store.get_bytes(parsed.bucket, parsed.key))


def _artifact_key_uri(s3_uri: str, filename: str) -> str:
    return f"{s3_uri.rstrip('/')}/{filename}"


def info_as_dict(info: ModelInfo) -> dict[str, Any]:
    return asdict(info)
