from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse

from ecg_benchmark.artifacts import ArtifactLayout
from ecg_benchmark.artifacts import parse_s3_uri
from ecg_benchmark.catalog import CatalogRecording
from ecg_benchmark.config import BenchmarkConfig
from ecg_benchmark.features import rr_interval_features, select_single_lead
from ecg_benchmark.labels import episodes_from_annotation_json, labels_for_recording
from ecg_benchmark.progress import ProgressReporter
from ecg_benchmark.runtime import ensure_java_home
from ecg_benchmark.storage import S3ArtifactStore


@dataclass(frozen=True)
class FeatureBuildResult:
    """Output of Spark feature generation for one dataset."""

    dataset_name: str
    feature_uri: str
    waveform_uri: str
    source_recordings: int
    labeled_windows: int
    windows_uri: str | None = None
    recording_manifest_uri: str | None = None
    cache_key: str | None = None
    reused: bool = False


def build_features_with_spark(
    config: BenchmarkConfig,
    layout: ArtifactLayout,
    recordings: list[CatalogRecording],
    *,
    feature_roots: dict[str, str] | None = None,
    progress: ProgressReporter | None = None,
) -> list[FeatureBuildResult]:
    """Build window/feature parquet datasets with PySpark.

    This first implementation creates the Spark session and writes a manifest of
    the intended per-dataset feature roots. The heavy ECG signal windowing is
    deliberately isolated here so it can evolve without changing the CLI or
    artifact contract.
    """

    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise RuntimeError("pyspark is required for build-features") from exc

    if not ensure_java_home(os.environ):
        raise RuntimeError(
            "Spark feature building requires a Java runtime before PySpark can start. "
            "Install Java and set JAVA_HOME, or run the benchmark from the spark-eda Docker service."
        )

    from pyspark.sql import DataFrame
    from pyspark.sql import functions as F

    spark_builder = SparkSession.builder.appName(config.spark.app_name)
    spark_master = os.environ.get(config.spark.master_env)
    if spark_master:
        spark_builder = spark_builder.master(spark_master)
    if config.spark.driver_memory:
        spark_builder = spark_builder.config("spark.driver.memory", config.spark.driver_memory)
    if config.spark.executor_memory:
        spark_builder = spark_builder.config("spark.executor.memory", config.spark.executor_memory)
    _configure_s3a(spark_builder, config)

    spark = spark_builder.getOrCreate()
    store = S3ArtifactStore.from_env(config.storage)
    try:
        feature_roots = feature_roots or {}
        results: list[FeatureBuildResult] = []
        for dataset_name in sorted({record.dataset_name for record in recordings}):
            stage_id = progress.start(
                "features",
                f"Build feature and waveform windows for {dataset_name}",
                dataset_name=dataset_name,
            ) if progress else None
            dataset_recordings = [record for record in recordings if record.dataset_name == dataset_name]
            feature_uri = feature_roots.get(dataset_name, layout.feature_dataset_uri(dataset_name))
            recording_manifest_uri = f"{feature_uri.rstrip('/')}/recording_manifest.parquet"
            windows_uri = f"{feature_uri.rstrip('/')}/windows.parquet"
            waveform_uri = f"{feature_uri.rstrip('/')}/waveform_windows.parquet"
            manifest_rows = [
                {
                    "dataset_name": record.dataset_name,
                    "full_recording_id": record.full_recording_id,
                    "recording_name": record.recording_name,
                    "parquet_path": record.parquet_path,
                    "annotations_path": record.annotations_path,
                    "duration_seconds": float(record.duration_seconds),
                    "sample_rate_hz": int(record.sampling_rate),
                    "window_seconds": int(config.data.window_seconds),
                    "stride_seconds": int(config.data.stride_seconds),
                    "label_policy": config.data.label_policy,
                    "lead_policy": config.data.lead_policy,
                }
                for record in dataset_recordings
            ]
            if manifest_rows:
                dataframe = spark.createDataFrame(manifest_rows)
                dataframe.write.mode("overwrite").parquet(_to_s3a(recording_manifest_uri))

            feature_buffer: list[DataFrame] = []
            feature_written = False
            waveform_rows_all: list[dict] = []
            labeled_windows = 0

            def flush_feature_buffer() -> None:
                nonlocal feature_written
                if not feature_buffer:
                    return
                batch = feature_buffer[0]
                for frame in feature_buffer[1:]:
                    batch = batch.unionByName(frame)
                mode = "append" if feature_written else "overwrite"
                batch.write.mode(mode).parquet(_to_s3a(windows_uri))
                feature_buffer.clear()
                feature_written = True

            for record in dataset_recordings:
                if not record.parquet_path or not record.annotations_path:
                    continue
                annotation_payload = _load_annotation_json(store, record.annotations_path)
                window_rows = _window_rows(record, annotation_payload, config)
                if not window_rows:
                    continue
                signal = spark.read.parquet(_to_s3a(record.parquet_path))
                lead = select_single_lead(signal.columns, config.data.preferred_leads)
                waveform_rows = [
                    _waveform_manifest_row(window_row, record, lead.column_name, lead.lead_name)
                    for window_row in window_rows
                ]
                if waveform_rows:
                    waveform_rows_all.extend(waveform_rows)
                windows = (
                    spark.createDataFrame(window_rows)
                    .withColumn("lead_column", F.lit(lead.column_name))
                    .withColumn("lead_name", F.lit(lead.lead_name))
                )
                joined = signal.select(
                    F.col("sample_index").cast("long").alias("sample_index"),
                    F.col(lead.column_name).cast("double").alias("signal_value"),
                ).join(
                    F.broadcast(windows),
                    (F.col("sample_index") >= F.col("start_sample"))
                    & (F.col("sample_index") < F.col("end_sample")),
                    "inner",
                )
                group_columns = [
                    "dataset_name",
                    "full_recording_id",
                    "recording_name",
                    "subject_id",
                    "window_index",
                    "window_start_seconds",
                    "window_end_seconds",
                    "start_sample",
                    "end_sample",
                    "label",
                    "rhythm",
                    "lead_column",
                    "lead_name",
                    "rr_mean",
                    "rr_std",
                    "rr_cv",
                    "rr_count",
                ]
                features = joined.groupBy(*group_columns).agg(
                    F.avg("signal_value").alias("signal_mean"),
                    F.stddev_pop("signal_value").alias("signal_std"),
                    F.min("signal_value").alias("signal_min"),
                    F.max("signal_value").alias("signal_max"),
                )
                features = features.withColumn("signal_range", F.col("signal_max") - F.col("signal_min"))
                feature_buffer.append(features)
                if len(feature_buffer) >= int(os.environ.get("ECG_BENCH_FEATURE_WRITE_BATCH", "25")):
                    flush_feature_buffer()
                labeled_windows += len(window_rows)

            flush_feature_buffer()
            if waveform_rows_all:
                waveform_dataframe = spark.createDataFrame(waveform_rows_all)
                waveform_dataframe.write.mode("overwrite").parquet(_to_s3a(waveform_uri))
            results.append(
                FeatureBuildResult(
                    dataset_name=dataset_name,
                    feature_uri=feature_uri,
                    waveform_uri=waveform_uri,
                    source_recordings=len(dataset_recordings),
                    labeled_windows=labeled_windows,
                    windows_uri=windows_uri,
                    recording_manifest_uri=recording_manifest_uri,
                )
            )
            if progress and stage_id:
                progress.finish(
                    stage_id,
                    status="succeeded",
                    dataset_name=dataset_name,
                    feature_uri=feature_uri,
                    windows_uri=windows_uri,
                    waveform_uri=waveform_uri,
                    source_recordings=len(dataset_recordings),
                    labeled_windows=labeled_windows,
                )
        return results
    finally:
        spark.stop()


def _configure_s3a(builder, config: BenchmarkConfig) -> None:
    endpoint = os.environ.get(config.storage.endpoint_env)
    access_key = os.environ.get(config.storage.access_key_env)
    secret_key = os.environ.get(config.storage.secret_key_env)
    if endpoint:
        parsed_endpoint = urlparse(endpoint)
        s3a_endpoint = parsed_endpoint.netloc or parsed_endpoint.path
        builder.config("spark.hadoop.fs.s3a.endpoint", s3a_endpoint)
        builder.config("spark.hadoop.fs.s3a.path.style.access", "true")
        builder.config("spark.hadoop.fs.s3a.connection.ssl.enabled", str(parsed_endpoint.scheme == "https").lower())
    if access_key:
        builder.config("spark.hadoop.fs.s3a.access.key", access_key)
    if secret_key:
        builder.config("spark.hadoop.fs.s3a.secret.key", secret_key)
    builder.config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")


def _to_s3a(uri: str) -> str:
    if uri.startswith("s3://"):
        return "s3a://" + uri[5:]
    return uri


def _load_annotation_json(store: S3ArtifactStore, annotations_path: str) -> dict:
    parsed = parse_s3_uri(annotations_path)
    return json.loads(store.get_bytes(parsed.bucket, parsed.key).decode("utf-8"))


def _window_rows(record: CatalogRecording, annotation_payload: dict, config: BenchmarkConfig) -> list[dict]:
    episodes = episodes_from_annotation_json(annotation_payload, sample_rate_hz=record.sampling_rate)
    labels = labels_for_recording(
        duration_seconds=record.duration_seconds,
        episodes=episodes,
        window_seconds=config.data.window_seconds,
        stride_seconds=config.data.stride_seconds,
        policy=config.data.label_policy,
    )
    beat_positions = (((annotation_payload.get("beat_annotations") or {}).get("positions")) or [])
    rows: list[dict] = []
    for window_index, label in enumerate(labels):
        if label.label is None:
            continue
        start_sample = int(round(label.start_seconds * record.sampling_rate))
        end_sample = int(round(label.end_seconds * record.sampling_rate))
        rr_features = _rr_features_for_window(beat_positions, start_sample, end_sample, record.sampling_rate)
        rows.append(
            {
                "dataset_name": record.dataset_name,
                "full_recording_id": record.full_recording_id,
                "recording_name": record.recording_name,
                "subject_id": str(record.subject_id or ""),
                "window_index": int(window_index),
                "window_start_seconds": float(label.start_seconds),
                "window_end_seconds": float(label.end_seconds),
                "start_sample": int(start_sample),
                "end_sample": int(end_sample),
                "label": int(label.label),
                "rhythm": str(label.rhythm),
                "lead_column": "",
                "lead_name": "",
                **rr_features,
            }
        )
    return rows


def _waveform_manifest_row(
    window_row: dict,
    record: CatalogRecording,
    lead_column: str,
    lead_name: str,
) -> dict:
    """Create a raw-waveform window pointer without materializing the samples."""

    start_sample = int(window_row["start_sample"])
    end_sample = int(window_row["end_sample"])
    return {
        "dataset_name": record.dataset_name,
        "full_recording_id": record.full_recording_id,
        "recording_name": record.recording_name,
        "subject_id": str(record.subject_id or ""),
        "window_index": int(window_row["window_index"]),
        "window_start_seconds": float(window_row["window_start_seconds"]),
        "window_end_seconds": float(window_row["window_end_seconds"]),
        "start_sample": start_sample,
        "end_sample": end_sample,
        "window_samples": int(max(0, end_sample - start_sample)),
        "sample_rate_hz": int(record.sampling_rate),
        "label": int(window_row["label"]),
        "rhythm": str(window_row["rhythm"]),
        "parquet_path": str(record.parquet_path),
        "annotations_path": str(record.annotations_path),
        "lead_column": str(lead_column),
        "lead_name": str(lead_name),
    }


def _rr_features_for_window(
    beat_positions: list[int],
    start_sample: int,
    end_sample: int,
    sample_rate_hz: int,
) -> dict[str, float]:
    positions = sorted(int(pos) for pos in beat_positions if start_sample <= int(pos) < end_sample)
    intervals = [
        (right - left) / float(sample_rate_hz)
        for left, right in zip(positions, positions[1:])
        if right > left
    ]
    return rr_interval_features(intervals)
