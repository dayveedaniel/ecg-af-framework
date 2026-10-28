from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from ecg_benchmark.config import BenchmarkConfig, DEFAULT_CACHE_PREFIX


_SAFE_RUN_CHARS = re.compile(r"[^A-Za-z0-9_.=-]+")
_SAFE_ARTIFACT_CHARS = re.compile(r"[^A-Za-z0-9_.=-]+")


@dataclass(frozen=True)
class ArtifactLayout:
    """Canonical S3 path layout for one benchmark run."""

    bucket: str
    prefix: str
    run_id: str
    cache_prefix: str = DEFAULT_CACHE_PREFIX

    @classmethod
    def from_config(cls, config: BenchmarkConfig, run_id: str) -> "ArtifactLayout":
        return cls(
            bucket=config.storage.artifact_bucket,
            prefix=config.storage.artifact_prefix,
            cache_prefix=config.storage.cache_prefix,
            run_id=sanitize_run_id(run_id),
        )

    @property
    def run_prefix(self) -> str:
        return "/".join(part.strip("/") for part in [self.prefix, self.run_id] if part.strip("/"))

    @property
    def run_uri(self) -> str:
        return f"s3://{self.bucket}/{self.run_prefix}/"

    def key(self, *parts: str) -> str:
        tail = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
        return f"{self.run_prefix}/{tail}" if tail else self.run_prefix

    def uri(self, *parts: str) -> str:
        return f"s3://{self.bucket}/{self.key(*parts)}"

    @property
    def cache_root_prefix(self) -> str:
        return self.cache_prefix.strip("/")

    @property
    def cache_root_uri(self) -> str:
        return f"s3://{self.bucket}/{self.cache_root_prefix}/"

    def cache_key(self, *parts: str) -> str:
        tail = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
        return f"{self.cache_root_prefix}/{tail}" if tail else self.cache_root_prefix

    def cache_uri(self, *parts: str) -> str:
        return f"s3://{self.bucket}/{self.cache_key(*parts)}"

    def resolved_config_uri(self) -> str:
        return self.uri("config", "resolved_config.json")

    def config_reference_uri(self) -> str:
        return self.uri("config", "benchmark_config_reference.md")

    def split_manifest_uri(self) -> str:
        return self.uri("splits", "split_manifest.json")

    def feature_dataset_uri(self, dataset_name: str) -> str:
        return self.uri("features", dataset_name)

    def feature_windows_uri(self, dataset_name: str) -> str:
        return self.uri("features", dataset_name, "windows.parquet")

    def waveform_windows_uri(self, dataset_name: str) -> str:
        return self.uri("features", dataset_name, "waveform_windows.parquet")

    def split_cache_dataset_uri(self, dataset_name: str, cache_key: str) -> str:
        return self.cache_uri("splits", sanitize_artifact_part(dataset_name), cache_key)

    def split_cache_manifest_uri(self, dataset_name: str, cache_key: str) -> str:
        return self.cache_uri("splits", sanitize_artifact_part(dataset_name), cache_key, "split_manifest.json")

    def feature_cache_dataset_uri(self, dataset_name: str, cache_key: str) -> str:
        return self.cache_uri("features", sanitize_artifact_part(dataset_name), cache_key)

    def feature_cache_manifest_uri(self, dataset_name: str, cache_key: str) -> str:
        return self.cache_uri("features", sanitize_artifact_part(dataset_name), cache_key, "feature_manifest.json")

    def model_uri(self, source_dataset: str, model_id: str) -> str:
        return self.uri("models", source_dataset, model_id)

    def window_predictions_uri(self, source_dataset: str, target_dataset: str, model_id: str) -> str:
        return self.uri("predictions", "windows", source_dataset, target_dataset, f"{model_id}.parquet")

    def recording_predictions_uri(self, source_dataset: str, target_dataset: str, model_id: str) -> str:
        return self.uri("predictions", "recordings", source_dataset, target_dataset, f"{model_id}.parquet")

    def metrics_json_uri(self) -> str:
        return self.uri("metrics", "metrics.json")

    def metrics_csv_uri(self) -> str:
        return self.uri("metrics", "metrics.csv")

    def evaluation_metrics_json_uri(self, evaluation_level: str) -> str:
        return self.uri("metrics", f"{sanitize_artifact_part(evaluation_level)}_metrics.json")

    def evaluation_metrics_csv_uri(self, evaluation_level: str) -> str:
        return self.uri("metrics", f"{sanitize_artifact_part(evaluation_level)}_metrics.csv")

    def timings_json_uri(self) -> str:
        return self.uri("timings", "timings.json")

    def timings_csv_uri(self) -> str:
        return self.uri("timings", "timings.csv")

    def matrix_csv_uri(self) -> str:
        return self.uri("matrix", "benchmark_matrix.csv")

    def evaluation_matrix_csv_uri(self, evaluation_level: str) -> str:
        return self.uri("matrix", f"{sanitize_artifact_part(evaluation_level)}_benchmark_matrix.csv")

    def run_summary_uri(self) -> str:
        return self.uri("RUN_SUMMARY.md")


@dataclass(frozen=True)
class ParsedS3Uri:
    bucket: str
    key: str


def parse_s3_uri(uri: str) -> ParsedS3Uri:
    if not uri.startswith("s3://"):
        raise ValueError(f"Expected an s3:// URI, got: {uri}")
    without_scheme = uri[5:]
    bucket, sep, key = without_scheme.partition("/")
    if not bucket or not sep or not key:
        raise ValueError(f"Expected URI in the form s3://bucket/key, got: {uri}")
    return ParsedS3Uri(bucket=bucket, key=key)


def sanitize_run_id(value: str) -> str:
    text = _SAFE_RUN_CHARS.sub("-", value.strip())
    return text.strip("-") or "run"


def sanitize_artifact_part(value: str) -> str:
    text = _SAFE_ARTIFACT_CHARS.sub("_", value.strip().lower())
    return text.strip("_") or "artifact"


def make_run_id(config: BenchmarkConfig, *, now: dt.datetime | None = None) -> str:
    """Create a readable run id that still includes the resolved config hash."""

    now = now or dt.datetime.now(dt.timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    name = sanitize_run_id(config.experiment.name)
    short_hash = config.config_hash()[:12]
    return f"{timestamp}-{name}-{short_hash}"
