from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ecg_benchmark.labels import SUPPORTED_LABEL_POLICIES


DEFAULT_WINDOW_SECONDS = 10
DEFAULT_STRIDE_SECONDS = 5
DEFAULT_LABEL_POLICY = "af_vs_nsr"
DEFAULT_LEAD_POLICY = "single_preferred_lead"
DEFAULT_RECORDING_AGGREGATION = "mean"
DEFAULT_RECORDING_LABEL_POLICY = "fraction_threshold"
DEFAULT_RECORDING_LABEL_THRESHOLD = 0.5
DEFAULT_ARTIFACT_BUCKET = "ecg-pipeline"
DEFAULT_ARTIFACT_PREFIX = "benchmarks/runs"
DEFAULT_CACHE_PREFIX = "benchmarks/cache"
DEFAULT_PROCESSED_PREFIX = "processed"
DEFAULT_SPLIT_RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}
DEFAULT_METRICS = [
    "n",
    "positive",
    "negative",
    "auroc",
    "average_precision",
    "accuracy",
    "f1",
    "sensitivity",
    "specificity",
]
DEFAULT_MODELS = [
    "dummy_prior",
    "rr_irregularity_rule",
    "logistic_regression_features",
    "random_forest_features",
]
DSP_MODEL_IDS = {
    "tateno_glass_rr_histogram",
    "lake_moorman_cosen",
    "petrenas_low_complexity_detector",
}
FEATURE_MODEL_IDS = {
    "zabihi_rf_handcrafted",
    "bin_adaboost_tree_ensemble",
    "goodfellow_xgboost_multifeature",
}
RAW_MODEL_IDS = {
    "raw_cnn_1d",
    "xiong_skip_cnn_1d",
    "zihlmann_crnn_spectrogram",
    "cpsc2021_resnet_transformer",
}
DEFAULT_PREFERRED_LEADS = ["II", "MLII", "I", "V1"]
REQUIRED_ENV_VARS = [
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
]
SUPPORTED_LEAD_POLICIES = {"single_preferred_lead"}
SUPPORTED_RECORDING_AGGREGATIONS = {"mean", "max", "majority_vote"}
SUPPORTED_RECORDING_LABEL_POLICIES = {"any", "majority", "fraction_threshold"}
SUPPORTED_SPLIT_STRATIFICATIONS = {"af_present", "dataset", "dataset_af_present", "none"}
SUPPORTED_REUSE_MODES = {"auto", "require", "off"}
SUPPORTED_MODEL_IDS = set(DEFAULT_MODELS) | DSP_MODEL_IDS | FEATURE_MODEL_IDS | RAW_MODEL_IDS
SUPPORTED_METRICS = set(DEFAULT_METRICS) | {
    "precision",
    "recall",
    "npv",
    "balanced_accuracy",
}


class ConfigError(ValueError):
    """Raised when a benchmark config is invalid.

    ``errors`` contains user-facing messages suitable for CLI output.
    """

    def __init__(self, errors: list[str] | str) -> None:
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class SplitConfig:
    """Train/validation/test split behavior.

    Ratios are applied after grouping by ``subject_id`` when available and by
    recording otherwise. This prevents subject leakage where the catalog gives
    enough metadata to avoid it.
    """

    train: float = DEFAULT_SPLIT_RATIOS["train"]
    val: float = DEFAULT_SPLIT_RATIOS["val"]
    test: float = DEFAULT_SPLIT_RATIOS["test"]
    stratify_by: str = "af_present"

    @classmethod
    def from_mapping(cls, raw: MappingLike | None) -> "SplitConfig":
        raw = raw or {}
        return cls(
            train=_as_float(raw.get("train", DEFAULT_SPLIT_RATIOS["train"]), "data.split.train"),
            val=_as_float(raw.get("val", DEFAULT_SPLIT_RATIOS["val"]), "data.split.val"),
            test=_as_float(raw.get("test", DEFAULT_SPLIT_RATIOS["test"]), "data.split.test"),
            stratify_by=str(raw.get("stratify_by", "af_present")),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        for name, value in [("train", self.train), ("val", self.val), ("test", self.test)]:
            if value < 0 or value > 1:
                errors.append(f"data.split.{name} must be between 0 and 1")
        if abs((self.train + self.val + self.test) - 1.0) > 1e-6:
            errors.append("data.split train/val/test ratios must sum to 1.0")
        if self.stratify_by not in SUPPORTED_SPLIT_STRATIFICATIONS:
            errors.append(f"data.split.stratify_by must be one of {sorted(SUPPORTED_SPLIT_STRATIFICATIONS)}")
        return errors


@dataclass(frozen=True)
class DataConfig:
    """Input datasets and window generation settings."""

    source_datasets: list[str]
    target_datasets: list[str]
    sample_rate_hz: int = 250
    processed_prefix: str = DEFAULT_PROCESSED_PREFIX
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    stride_seconds: int = DEFAULT_STRIDE_SECONDS
    label_policy: str = DEFAULT_LABEL_POLICY
    lead_policy: str = DEFAULT_LEAD_POLICY
    preferred_leads: list[str] = field(default_factory=lambda: list(DEFAULT_PREFERRED_LEADS))
    recording_aggregation: str = DEFAULT_RECORDING_AGGREGATION
    recording_label_policy: str = DEFAULT_RECORDING_LABEL_POLICY
    recording_label_threshold: float = DEFAULT_RECORDING_LABEL_THRESHOLD
    split: SplitConfig = field(default_factory=SplitConfig)

    @classmethod
    def from_mapping(cls, raw: MappingLike | None) -> "DataConfig":
        raw = raw or {}
        source = _string_list(raw.get("source_datasets", raw.get("datasets", [])), "data.source_datasets")
        target = _string_list(raw.get("target_datasets", source), "data.target_datasets")
        return cls(
            source_datasets=source,
            target_datasets=target,
            sample_rate_hz=_as_int(raw.get("sample_rate_hz", 250), "data.sample_rate_hz"),
            processed_prefix=str(raw.get("processed_prefix", DEFAULT_PROCESSED_PREFIX)).strip("/"),
            window_seconds=_as_int(raw.get("window_seconds", DEFAULT_WINDOW_SECONDS), "data.window_seconds"),
            stride_seconds=_as_int(raw.get("stride_seconds", DEFAULT_STRIDE_SECONDS), "data.stride_seconds"),
            label_policy=str(raw.get("label_policy", DEFAULT_LABEL_POLICY)),
            lead_policy=str(raw.get("lead_policy", DEFAULT_LEAD_POLICY)),
            preferred_leads=_string_list(
                raw.get("preferred_leads", DEFAULT_PREFERRED_LEADS),
                "data.preferred_leads",
            ),
            recording_aggregation=str(raw.get("recording_aggregation", DEFAULT_RECORDING_AGGREGATION)),
            recording_label_policy=str(raw.get("recording_label_policy", DEFAULT_RECORDING_LABEL_POLICY)),
            recording_label_threshold=_as_float(
                raw.get("recording_label_threshold", DEFAULT_RECORDING_LABEL_THRESHOLD),
                "data.recording_label_threshold",
            ),
            split=SplitConfig.from_mapping(_mapping(raw.get("split"), "data.split", allow_none=True)),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.source_datasets:
            errors.append("data.source_datasets must contain at least one dataset name")
        if not self.target_datasets:
            errors.append("data.target_datasets must contain at least one dataset name")
        if self.sample_rate_hz <= 0:
            errors.append("data.sample_rate_hz must be positive")
        if self.window_seconds <= 0:
            errors.append("data.window_seconds must be positive")
        if self.stride_seconds <= 0:
            errors.append("data.stride_seconds must be positive")
        if self.stride_seconds > self.window_seconds:
            errors.append("data.stride_seconds should not exceed data.window_seconds")
        if self.label_policy not in SUPPORTED_LABEL_POLICIES:
            errors.append(f"data.label_policy must be one of {sorted(SUPPORTED_LABEL_POLICIES)}")
        if self.lead_policy not in SUPPORTED_LEAD_POLICIES:
            errors.append(f"data.lead_policy must be one of {sorted(SUPPORTED_LEAD_POLICIES)}")
        if not self.preferred_leads:
            errors.append("data.preferred_leads must contain at least one lead name")
        if self.recording_aggregation not in SUPPORTED_RECORDING_AGGREGATIONS:
            errors.append(
                f"data.recording_aggregation must be one of {sorted(SUPPORTED_RECORDING_AGGREGATIONS)}"
            )
        if self.recording_label_policy not in SUPPORTED_RECORDING_LABEL_POLICIES:
            errors.append(
                f"data.recording_label_policy must be one of {sorted(SUPPORTED_RECORDING_LABEL_POLICIES)}"
            )
        if self.recording_label_threshold < 0 or self.recording_label_threshold > 1:
            errors.append("data.recording_label_threshold must be between 0 and 1")
        errors.extend(self.split.validate())
        return errors


@dataclass(frozen=True)
class StorageConfig:
    """Artifact and source storage settings.

    Artifact paths resolve to
    ``s3://<artifact_bucket>/<artifact_prefix>/<run_id>/...``.
    """

    artifact_bucket: str = DEFAULT_ARTIFACT_BUCKET
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX
    cache_prefix: str = DEFAULT_CACHE_PREFIX
    source_bucket: str = DEFAULT_ARTIFACT_BUCKET
    endpoint_env: str = "S3_ENDPOINT_URL"
    access_key_env: str = "S3_ACCESS_KEY"
    secret_key_env: str = "S3_SECRET_KEY"

    @classmethod
    def from_mapping(cls, raw: MappingLike | None) -> "StorageConfig":
        raw = raw or {}
        return cls(
            artifact_bucket=str(raw.get("artifact_bucket", DEFAULT_ARTIFACT_BUCKET)),
            artifact_prefix=str(raw.get("artifact_prefix", DEFAULT_ARTIFACT_PREFIX)).strip("/"),
            cache_prefix=str(raw.get("cache_prefix", DEFAULT_CACHE_PREFIX)).strip("/"),
            source_bucket=str(raw.get("source_bucket", raw.get("artifact_bucket", DEFAULT_ARTIFACT_BUCKET))),
            endpoint_env=str(raw.get("endpoint_env", "S3_ENDPOINT_URL")),
            access_key_env=str(raw.get("access_key_env", "S3_ACCESS_KEY")),
            secret_key_env=str(raw.get("secret_key_env", "S3_SECRET_KEY")),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.artifact_bucket:
            errors.append("storage.artifact_bucket is required")
        if not self.artifact_prefix:
            errors.append("storage.artifact_prefix is required")
        if not self.cache_prefix:
            errors.append("storage.cache_prefix is required")
        if not self.source_bucket:
            errors.append("storage.source_bucket is required")
        return errors


@dataclass(frozen=True)
class ReuseConfig:
    """Dataset-level cache reuse behavior for splits and generated windows."""

    enabled: bool = True
    splits: bool = True
    features: bool = True
    mode: str = "auto"

    @classmethod
    def from_mapping(cls, raw: MappingLike | None) -> "ReuseConfig":
        raw = raw or {}
        return cls(
            enabled=_as_bool(raw.get("enabled", True), "reuse.enabled"),
            splits=_as_bool(raw.get("splits", True), "reuse.splits"),
            features=_as_bool(raw.get("features", True), "reuse.features"),
            mode=str(raw.get("mode", "auto")),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.mode not in SUPPORTED_REUSE_MODES:
            errors.append(f"reuse.mode must be one of {sorted(SUPPORTED_REUSE_MODES)}")
        return errors


@dataclass(frozen=True)
class DatabaseConfig:
    """Postgres environment variable names used by the benchmark catalog."""

    host_env: str = "POSTGRES_HOST"
    port_env: str = "POSTGRES_PORT"
    db_env: str = "POSTGRES_DB"
    user_env: str = "POSTGRES_USER"
    password_env: str = "POSTGRES_PASSWORD"

    @classmethod
    def from_mapping(cls, raw: MappingLike | None) -> "DatabaseConfig":
        raw = raw or {}
        return cls(
            host_env=str(raw.get("host_env", "POSTGRES_HOST")),
            port_env=str(raw.get("port_env", "POSTGRES_PORT")),
            db_env=str(raw.get("db_env", "POSTGRES_DB")),
            user_env=str(raw.get("user_env", "POSTGRES_USER")),
            password_env=str(raw.get("password_env", "POSTGRES_PASSWORD")),
        )

    def required_env_vars(self) -> list[str]:
        return [self.host_env, self.port_env, self.db_env, self.user_env, self.password_env]


@dataclass(frozen=True)
class SparkConfig:
    """Spark settings for window and feature generation."""

    master_env: str = "SPARK_MASTER"
    app_name: str = "ecg-af-benchmark"
    driver_memory: str | None = None
    executor_memory: str | None = None

    @classmethod
    def from_mapping(cls, raw: MappingLike | None) -> "SparkConfig":
        raw = raw or {}
        return cls(
            master_env=str(raw.get("master_env", "SPARK_MASTER")),
            app_name=str(raw.get("app_name", "ecg-af-benchmark")),
            driver_memory=_optional_str(raw.get("driver_memory")),
            executor_memory=_optional_str(raw.get("executor_memory")),
        )


@dataclass(frozen=True)
class ModelConfig:
    """One model entry from the experiment config."""

    id: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, raw: Any, index: int) -> "ModelConfig":
        if isinstance(raw, str):
            return cls(id=raw)
        if isinstance(raw, dict):
            model_id = raw.get("id")
            if not model_id:
                raise ConfigError(f"models[{index}].id is required")
            params = raw.get("params", {})
            if not isinstance(params, dict):
                raise ConfigError(f"models[{index}].params must be a mapping")
            return cls(id=str(model_id), params=dict(params))
        raise ConfigError(f"models[{index}] must be a string or mapping")

    def validate(self) -> list[str]:
        if self.id not in SUPPORTED_MODEL_IDS:
            return [f"Unknown model id '{self.id}'. Supported model ids: {sorted(SUPPORTED_MODEL_IDS)}"]
        return []


@dataclass(frozen=True)
class ExperimentInfo:
    """Human-facing experiment metadata."""

    name: str = "af-detection-benchmark"
    description: str | None = None

    @classmethod
    def from_mapping(cls, raw: MappingLike | str | None) -> "ExperimentInfo":
        if raw is None:
            return cls()
        if isinstance(raw, str):
            return cls(name=raw)
        raw = _mapping(raw, "experiment")
        return cls(name=str(raw.get("name", "af-detection-benchmark")), description=_optional_str(raw.get("description")))


@dataclass(frozen=True)
class BenchmarkConfig:
    """Resolved benchmark configuration.

    ``random_seed`` is intentionally required at the top level. It controls
    splits and model initialization so every run can be reproduced.
    """

    random_seed: int
    experiment: ExperimentInfo = field(default_factory=ExperimentInfo)
    data: DataConfig = field(default_factory=lambda: DataConfig(source_datasets=[], target_datasets=[]))
    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    spark: SparkConfig = field(default_factory=SparkConfig)
    reuse: ReuseConfig = field(default_factory=ReuseConfig)
    models: list[ModelConfig] = field(default_factory=lambda: [ModelConfig(id=item) for item in DEFAULT_MODELS])
    metrics: list[str] = field(default_factory=lambda: list(DEFAULT_METRICS))

    @classmethod
    def from_mapping(cls, raw: MappingLike) -> "BenchmarkConfig":
        raw = _mapping(raw, "config")
        _validate_top_level_keys(raw)
        if "random_seed" not in raw:
            raise ConfigError("random_seed is required at the top level")
        seed = _as_int(raw["random_seed"], "random_seed")
        model_values = raw.get("models", DEFAULT_MODELS)
        if not isinstance(model_values, list):
            raise ConfigError("models must be a list of model ids or model mappings")
        metrics = _string_list(raw.get("metrics", DEFAULT_METRICS), "metrics")
        return cls(
            random_seed=seed,
            experiment=ExperimentInfo.from_mapping(raw.get("experiment")),
            data=DataConfig.from_mapping(_mapping(raw.get("data"), "data", allow_none=True)),
            storage=StorageConfig.from_mapping(_mapping(raw.get("storage"), "storage", allow_none=True)),
            database=DatabaseConfig.from_mapping(_mapping(raw.get("database"), "database", allow_none=True)),
            spark=SparkConfig.from_mapping(_mapping(raw.get("spark"), "spark", allow_none=True)),
            reuse=ReuseConfig.from_mapping(_mapping(raw.get("reuse"), "reuse", allow_none=True)),
            models=[ModelConfig.from_value(item, i) for i, item in enumerate(model_values)],
            metrics=metrics,
        )

    def validate(self) -> None:
        errors: list[str] = []
        if isinstance(self.random_seed, bool):
            errors.append("random_seed must be an integer, not a boolean")
        if self.random_seed < 0:
            errors.append("random_seed must be non-negative")
        errors.extend(self.data.validate())
        errors.extend(self.storage.validate())
        errors.extend(self.reuse.validate())
        if not self.models:
            errors.append("models must contain at least one model")
        for model in self.models:
            errors.extend(model.validate())
        unknown_metrics = sorted(set(self.metrics) - SUPPORTED_METRICS)
        if unknown_metrics:
            errors.append(f"Unknown metrics {unknown_metrics}. Supported metrics: {sorted(SUPPORTED_METRICS)}")
        if not self.metrics:
            errors.append("metrics must contain at least one metric")
        if errors:
            raise ConfigError(errors)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def required_env_vars(self) -> list[str]:
        names = {
            self.storage.endpoint_env,
            self.storage.access_key_env,
            self.storage.secret_key_env,
            *self.database.required_env_vars(),
        }
        return sorted(names)

    def missing_env_vars(self, env: dict[str, str] | None = None) -> list[str]:
        env = env or os.environ
        return [name for name in self.required_env_vars() if not env.get(name)]

    def processed_dataset_uri(self, dataset_name: str) -> str:
        prefix = self.data.processed_prefix.strip("/")
        return f"s3://{self.storage.source_bucket}/{prefix}/{self.data.sample_rate_hz}hz/{dataset_name}/"


def load_config(path: str | Path, *, validate: bool = True) -> BenchmarkConfig:
    """Load a documented benchmark YAML config and apply defaults."""

    path = Path(path)
    data = _load_yaml(path)
    config = BenchmarkConfig.from_mapping(data)
    if validate:
        config.validate()
    return config


def validate_env(config: BenchmarkConfig, env: dict[str, str] | None = None) -> None:
    missing = config.missing_env_vars(env)
    if missing:
        raise ConfigError([f"Missing required environment variable: {name}" for name in missing])


MappingLike = dict[str, Any]


def _load_yaml(path: Path) -> MappingLike:
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError("PyYAML is required to read benchmark YAML configs") from exc

    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("Config file must contain a top-level mapping")
    return loaded


def _validate_top_level_keys(raw: MappingLike) -> None:
    allowed = {"experiment", "random_seed", "data", "storage", "database", "spark", "reuse", "models", "metrics"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"Unknown top-level config keys: {unknown}")


def _mapping(value: Any, context: str, *, allow_none: bool = False) -> MappingLike | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a mapping")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{context} must be a list")
    items = [str(item).strip() for item in value]
    empty = [i for i, item in enumerate(items) if not item]
    if empty:
        raise ConfigError(f"{context} contains empty string values at indexes {empty}")
    return items


def _as_int(value: Any, context: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{context} must be an integer, not a boolean")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{context} must be an integer") from exc


def _as_float(value: Any, context: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{context} must be a number, not a boolean")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{context} must be a number") from exc


def _as_bool(value: Any, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    raise ConfigError(f"{context} must be a boolean")
