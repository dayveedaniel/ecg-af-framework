from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "py_ecg_benchmark_pkg"
EXAMPLES_DIR = PACKAGE_ROOT / "examples"
DEFAULT_GENERATED_CONFIG_DIR = PACKAGE_ROOT / "generated_configs"
DEFAULT_JOB_DIR = DEFAULT_GENERATED_CONFIG_DIR / "dashboard_jobs"
DEFAULT_DOCKER_JOB_DIR = PROJECT_ROOT / "experiments" / "data_pipeline_test" / "dashboard_jobs"
DEFAULT_DOCKER_COMPOSE_FILES = (
    PROJECT_ROOT / "data-pipeline" / "docker-compose.yaml",
    PROJECT_ROOT / "experiments" / "data_pipeline_test" / "docker-compose.spark.yaml",
)
DEFAULT_DOCKER_ENV_FILE = PROJECT_ROOT / "data-pipeline" / ".env.docker"
DEFAULT_DOCKER_SERVICE = "spark-eda"
DEFAULT_DOCKER_CONTAINER_JOB_DIR = "/home/jovyan/work/dashboard_jobs"
DEFAULT_RANDOM_SEED = 20260428
DEFAULT_LOG_DIRS = ("experiments/data_pipeline_test", "../experiments/data_pipeline_test")
EVALUATION_LEVELS = ("window", "recording")
BEST_METRIC_PRIORITY = ("auroc", "average_precision", "f1", "accuracy", "sensitivity", "specificity")
COUNT_METRICS = {"n", "positive", "negative"}
PRESENTED_ARTIFACTS = (
    ("Resolved config", ("config", "resolved_config.json")),
    ("Config reference", ("config", "benchmark_config_reference.md")),
    ("Run summary", ("RUN_SUMMARY.md",)),
    ("Split manifest", ("splits", "split_manifest.json")),
    ("Feature manifest", ("features", "feature_manifest.json")),
    ("Window metrics JSON", ("metrics", "window_metrics.json")),
    ("Window metrics CSV", ("metrics", "window_metrics.csv")),
    ("Recording metrics JSON", ("metrics", "recording_metrics.json")),
    ("Recording metrics CSV", ("metrics", "recording_metrics.csv")),
    ("Timing JSON", ("timings", "timings.json")),
    ("Timing CSV", ("timings", "timings.csv")),
    ("Window matrix", ("matrix", "window_benchmark_matrix.csv")),
    ("Recording matrix", ("matrix", "recording_benchmark_matrix.csv")),
)

__all__ = [
    "BEST_METRIC_PRIORITY",
    "COUNT_METRICS",
    "DEFAULT_GENERATED_CONFIG_DIR",
    "DEFAULT_DOCKER_CONTAINER_JOB_DIR",
    "DEFAULT_DOCKER_COMPOSE_FILES",
    "DEFAULT_DOCKER_ENV_FILE",
    "DEFAULT_DOCKER_JOB_DIR",
    "DEFAULT_DOCKER_SERVICE",
    "DEFAULT_JOB_DIR",
    "DEFAULT_LOG_DIRS",
    "DEFAULT_RANDOM_SEED",
    "EVALUATION_LEVELS",
    "EXAMPLES_DIR",
    "PRESENTED_ARTIFACTS",
]
