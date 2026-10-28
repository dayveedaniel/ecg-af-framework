from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from airflow.operators.python import get_current_context

from ecg_pipeline.config import load_pipeline_config
from ecg_pipeline.manifest import load_manifest
from ecg_pipeline.utils.s3 import S3Client


def load_cfg():
    env_path = os.environ.get("PIPELINE_CONFIG_PATH")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("/opt/ecg-pipeline/pipeline_config.yaml"))
    candidates.append(Path(__file__).resolve().parents[1] / "pipeline_config.yaml")

    for candidate in candidates:
        if candidate.exists():
            return load_pipeline_config(candidate)
    raise FileNotFoundError("Could not locate pipeline_config.yaml")


def as_bool(value: Any, *, key: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    raise TypeError(f"{key} must be a boolean")


def resolve_manifest_paths(*, manifests_dir: Path, all_manifests: bool, dataset_name: str | None) -> list[Path]:
    if all_manifests:
        manifest_paths = sorted(manifests_dir.glob("*.yaml"))
        if not manifest_paths:
            raise FileNotFoundError(f"No manifest files found in {manifests_dir}")
        return manifest_paths
    if not dataset_name:
        raise ValueError("Missing dataset_name (set all_manifests=true or provide dataset_name)")
    return [manifests_dir / f"{dataset_name}.yaml"]


def run_params_from_context() -> tuple[dict[str, Any], dict[str, Any]]:
    context = get_current_context()
    dag_run = context.get("dag_run")
    conf = dag_run.conf if dag_run and dag_run.conf else {}
    params = context.get("params", {}) or {}
    return conf, params


def manifests_dir() -> Path:
    return Path(os.environ.get("MANIFESTS_DIR", "/opt/ecg-pipeline/manifests"))


def load_dataset_manifest(dataset_name: str):
    return load_manifest(manifests_dir() / f"{dataset_name}.yaml")


def build_s3() -> S3Client:
    return S3Client(
        endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
        access_key=os.environ.get("S3_ACCESS_KEY"),
        secret_key=os.environ.get("S3_SECRET_KEY"),
    )


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"Expected s3:// URI, got '{uri}'")
    value = uri[len("s3://") :]
    if "/" not in value:
        raise ValueError(f"Invalid S3 URI (missing key): '{uri}'")
    bucket, key = value.split("/", 1)
    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: '{uri}'")
    return bucket, key
