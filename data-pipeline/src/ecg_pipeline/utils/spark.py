from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, MutableMapping


def detect_java_home(env: MutableMapping[str, str] | None = None) -> str | None:
    """Return a usable JAVA_HOME from env or a discovered Java install."""

    env = env or os.environ
    configured = str(env.get("JAVA_HOME") or "").strip()
    if configured:
        return configured

    java_path = shutil.which("java")
    if java_path:
        executable = Path(java_path).resolve()
        if executable.parent.name == "bin":
            java_home = executable.parent.parent
            if (java_home / "bin" / "java").exists():
                return str(java_home)

    for candidate in (
        Path("/usr/lib/jvm/default-java"),
        Path("/usr/lib/jvm/java-21-openjdk-amd64"),
        Path("/usr/lib/jvm/java-17-openjdk-amd64"),
        Path("/usr/lib/jvm/java-11-openjdk-amd64"),
        Path("/opt/homebrew/opt/openjdk"),
        Path("/usr/local/opt/openjdk"),
    ):
        if (candidate / "bin" / "java").exists():
            return str(candidate)
    return None


def ensure_java_home(env: MutableMapping[str, str] | None = None) -> str | None:
    """Populate JAVA_HOME when it can be inferred."""

    env = env or os.environ
    java_home = detect_java_home(env)
    if java_home and not str(env.get("JAVA_HOME") or "").strip():
        env["JAVA_HOME"] = java_home
    return java_home


def create_spark_session(
    *,
    app_name: str = "ecg-pipeline",
    master: str | None = None,
    driver_memory: str | None = None,
    executor_memory: str | None = None,
) -> Any:
    """Create a SparkSession for local/distributed recording-group processing."""

    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise RuntimeError(
            "pyspark is required for Spark-backed processing. "
            "Install the pipeline 'airflow' or 'benchmark' extra, or add pyspark."
        ) from exc

    if not ensure_java_home(os.environ):
        raise RuntimeError(
            "Spark processing requires a Java runtime before PySpark can start. "
            "Install Java and set JAVA_HOME."
        )

    builder = SparkSession.builder.appName(app_name)
    resolved_master = (master or os.environ.get("SPARK_MASTER") or "local[*]").strip()
    if resolved_master:
        builder = builder.master(resolved_master)
    if driver_memory:
        builder = builder.config("spark.driver.memory", driver_memory)
    if executor_memory:
        builder = builder.config("spark.executor.memory", executor_memory)
    # Avoid UI port collisions when multiple local jobs start.
    builder = builder.config("spark.ui.enabled", "false")
    return builder.getOrCreate()
