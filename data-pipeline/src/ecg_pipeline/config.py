from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ecg_pipeline.utils.env import load_dotenv_file


@dataclass(frozen=True)
class PipelineConfig:
    s3_bucket: str
    default_target_sample_rate: int
    default_normalization: str
    parquet_compression: str

    min_recordings_for_spark: int
    spark_driver_memory: str
    spark_executor_memory: str

    airflow_max_active_datasets: int
    airflow_task_retries: int
    airflow_retry_delay_seconds: int
    airflow_retry_exponential_backoff: bool

    download_timeout_seconds: int
    download_chunk_size_bytes: int
    download_sleep_between_datasets_seconds: float
    physionet_s3_bucket: str


@dataclass(frozen=True)
class EnvConfig:
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    spark_master: str | None = None


@dataclass(frozen=True)
class AppConfig:
    pipeline: PipelineConfig
    env: EnvConfig


def _require(mapping: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in mapping:
        raise KeyError(f"Missing required key '{key}' in {ctx}")
    return mapping[key]


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    pipeline = _require(data, "pipeline", f"{path}")
    processing = _require(data, "processing", f"{path}")
    airflow = _require(data, "airflow", f"{path}")
    download = _require(data, "download", f"{path}")

    return PipelineConfig(
        s3_bucket=str(_require(pipeline, "s3_bucket", "pipeline")),
        default_target_sample_rate=int(_require(pipeline, "default_target_sample_rate", "pipeline")),
        default_normalization=str(_require(pipeline, "default_normalization", "pipeline")),
        parquet_compression=str(_require(pipeline, "parquet_compression", "pipeline")),
        min_recordings_for_spark=int(_require(processing, "min_recordings_for_spark", "processing")),
        spark_driver_memory=str(_require(processing, "spark_driver_memory", "processing")),
        spark_executor_memory=str(_require(processing, "spark_executor_memory", "processing")),
        airflow_max_active_datasets=int(_require(airflow, "max_active_datasets", "airflow")),
        airflow_task_retries=int(_require(airflow, "task_retries", "airflow")),
        airflow_retry_delay_seconds=int(_require(airflow, "retry_delay_seconds", "airflow")),
        airflow_retry_exponential_backoff=bool(_require(airflow, "retry_exponential_backoff", "airflow")),
        download_timeout_seconds=int(_require(download, "timeout_seconds", "download")),
        download_chunk_size_bytes=int(_require(download, "chunk_size_bytes", "download")),
        download_sleep_between_datasets_seconds=float(
            _require(download, "sleep_between_datasets_seconds", "download")
        ),
        physionet_s3_bucket=str(_require(download, "physionet_s3_bucket", "download")),
    )


def load_env_config(env_path: str | Path) -> EnvConfig:
    env = load_dotenv_file(env_path)

    def require_env(name: str) -> str:
        value = env.get(name)
        if value is None or value == "":
            raise KeyError(f"Missing required env var '{name}' in {env_path}")
        return value

    spark_master = env.get("SPARK_MASTER") or None

    return EnvConfig(
        s3_endpoint_url=require_env("S3_ENDPOINT_URL"),
        s3_access_key=require_env("S3_ACCESS_KEY"),
        s3_secret_key=require_env("S3_SECRET_KEY"),
        postgres_host=require_env("POSTGRES_HOST"),
        postgres_port=int(require_env("POSTGRES_PORT")),
        postgres_db=require_env("POSTGRES_DB"),
        postgres_user=require_env("POSTGRES_USER"),
        postgres_password=require_env("POSTGRES_PASSWORD"),
        spark_master=spark_master,
    )


def load_app_config(pipeline_root: str | Path, *, env_filename: str = ".env") -> AppConfig:
    pipeline_root = Path(pipeline_root)
    pipeline_cfg = load_pipeline_config(pipeline_root / "pipeline_config.yaml")
    env_cfg = load_env_config(pipeline_root / env_filename)
    return AppConfig(pipeline=pipeline_cfg, env=env_cfg)


def default_pipeline_root() -> Path:
    # data-pipeline/src/ecg_pipeline/config.py -> data-pipeline/
    return Path(__file__).resolve().parents[3]
