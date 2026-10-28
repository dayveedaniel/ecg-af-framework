from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ecg_benchmark.artifacts import ArtifactLayout, make_run_id, parse_s3_uri
from ecg_benchmark.cache import feature_cache_key, feature_cache_payload, split_cache_key, split_cache_payload
from ecg_benchmark.catalog import BenchmarkCatalog
from ecg_benchmark.config import BenchmarkConfig, ConfigError, load_config, validate_env
from ecg_benchmark.metrics import BinaryPrediction, aggregate_by_recording, compute_binary_metrics
from ecg_benchmark.models import create_model, load_model
from ecg_benchmark.progress import ProgressReporter
from ecg_benchmark.reporting import config_reference_markdown, metrics_to_csv, run_summary_markdown
from ecg_benchmark.spark_features import build_features_with_spark
from ecg_benchmark.splits import SplitAssignment, make_deterministic_splits, split_manifest
from ecg_benchmark.storage import S3ArtifactStore

WINDOW_LEVEL = "window"
RECORDING_LEVEL = "recording"
EVALUATION_LEVELS = (WINDOW_LEVEL, RECORDING_LEVEL)


def validate_config_file(path: str | Path, *, check_env: bool = True, check_connections: bool = False) -> BenchmarkConfig:
    config = load_config(path, validate=True)
    if check_env:
        validate_env(config)
    if check_connections:
        catalog = BenchmarkCatalog.from_env(config.database)
        store = S3ArtifactStore.from_env(config.storage)
        try:
            catalog.ensure_schema()
            existing = set(catalog.dataset_names())
            missing = sorted((set(config.data.source_datasets) | set(config.data.target_datasets)) - existing)
            if missing:
                raise ConfigError([f"Dataset not found in Postgres catalog: {name}" for name in missing])
            store.ensure_bucket(config.storage.artifact_bucket)
        finally:
            catalog.close()
    return config


def initialize_run_artifacts(config: BenchmarkConfig, *, run_id: str | None = None) -> ArtifactLayout:
    run_id = run_id or make_run_id(config)
    layout = ArtifactLayout.from_config(config, run_id)
    store = S3ArtifactStore.from_env(config.storage)
    store.ensure_bucket(layout.bucket)
    resolved = parse_s3_uri(layout.resolved_config_uri())
    reference = parse_s3_uri(layout.config_reference_uri())
    summary = parse_s3_uri(layout.run_summary_uri())
    store.put_json(resolved.bucket, resolved.key, config.to_dict(), metadata={"config-hash": config.config_hash()})
    store.put_text(reference.bucket, reference.key, config_reference_markdown(), content_type="text/markdown; charset=utf-8")
    store.put_text(summary.bucket, summary.key, run_summary_markdown(config=config, layout=layout), content_type="text/markdown; charset=utf-8")
    return layout


def make_splits(
    config: BenchmarkConfig,
    *,
    run_id: str | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    layout = initialize_run_artifacts(config, run_id=run_id)
    catalog = BenchmarkCatalog.from_env(config.database)
    store = S3ArtifactStore.from_env(config.storage)
    try:
        catalog.ensure_schema()
        catalog.upsert_run(
            run_id=layout.run_id,
            experiment_name=config.experiment.name,
            random_seed=config.random_seed,
            config_hash=config.config_hash(),
            artifact_root_uri=layout.run_uri,
            resolved_config_uri=layout.resolved_config_uri(),
        )
        recordings = catalog.recordings_for_datasets(
            sorted(set(config.data.source_datasets) | set(config.data.target_datasets)),
            sample_rate_hz=config.data.sample_rate_hz,
        )
        recordings_by_dataset = _recordings_by_dataset(recordings)
        assignment_dicts: list[dict[str, Any]] = []
        cache_entries: list[dict[str, Any]] = []
        cache_enabled = _reuse_enabled(config, "splits")
        for dataset_name in sorted(set(config.data.source_datasets) | set(config.data.target_datasets)):
            stage_id = _progress_start(
                progress,
                "splits",
                f"Create or reuse split partition for {dataset_name}",
                dataset_name=dataset_name,
            )
            dataset_records = [record.to_split_record() for record in recordings_by_dataset.get(dataset_name, [])]
            cache_key = split_cache_key(config, dataset_name, dataset_records)
            cache_uri = layout.split_cache_dataset_uri(dataset_name, cache_key)
            cache_manifest_uri = layout.split_cache_manifest_uri(dataset_name, cache_key)
            dataset_manifest = _read_json_uri_if_exists(cache_manifest_uri, store) if cache_enabled else None
            reused = bool(
                dataset_manifest
                and dataset_manifest.get("cache_key") == cache_key
                and dataset_manifest.get("dataset_name") == dataset_name
            )
            if not reused:
                if cache_enabled and config.reuse.mode == "require":
                    raise RuntimeError(f"Required split cache entry is missing for dataset '{dataset_name}'")
                dataset_assignments = make_deterministic_splits(
                    dataset_records,
                    seed=config.random_seed,
                    train_ratio=config.data.split.train,
                    val_ratio=config.data.split.val,
                    test_ratio=config.data.split.test,
                    stratify_by=config.data.split.stratify_by,
                )
                dataset_manifest = split_manifest(
                    dataset_assignments,
                    seed=config.random_seed,
                    config_hash=config.config_hash(),
                    ratios=_split_ratios(config),
                )
                dataset_manifest.update(
                    {
                        "cache_key": cache_key,
                        "cache_payload": split_cache_payload(config, dataset_name, dataset_records),
                        "cache_uri": cache_uri,
                        "cache_manifest_uri": cache_manifest_uri,
                        "dataset_name": dataset_name,
                        "source_recordings": len(dataset_records),
                    }
                )
                if cache_enabled:
                    parsed_cache_manifest = parse_s3_uri(cache_manifest_uri)
                    store.put_json(
                        parsed_cache_manifest.bucket,
                        parsed_cache_manifest.key,
                        dataset_manifest,
                        metadata={"cache-key": cache_key, "cache-kind": "split"},
                    )
            _progress_finish(
                progress,
                stage_id,
                status="reused" if reused else "succeeded",
                dataset_name=dataset_name,
                cache_key=cache_key,
                cache_uri=cache_uri,
                source_recordings=len(dataset_records),
                counts=dataset_manifest.get("counts", {}),
            )
            assignment_dicts.extend(dataset_manifest.get("assignments", []))
            cache_entries.append(
                {
                    "kind": "splits",
                    "dataset_name": dataset_name,
                    "cache_key": cache_key,
                    "cache_uri": cache_uri,
                    "cache_manifest_uri": cache_manifest_uri,
                    "reused": reused,
                    "enabled": cache_enabled,
                    "source_recordings": len(dataset_records),
                    "counts": dataset_manifest.get("counts", {}),
                }
            )
        assignments = [_split_assignment_from_dict(item) for item in assignment_dicts]
        manifest = split_manifest(
            assignments,
            seed=config.random_seed,
            config_hash=config.config_hash(),
            ratios=_split_ratios(config),
        )
        manifest["cache"] = {
            "enabled": cache_enabled,
            "mode": config.reuse.mode,
            "entries": cache_entries,
        }
        split_uri = parse_s3_uri(layout.split_manifest_uri())
        store.put_json(split_uri.bucket, split_uri.key, manifest, metadata={"split-hash": manifest["split_hash"]})
        catalog.insert_split_assignments(layout.run_id, manifest["assignments"])
        catalog.upsert_run(
            run_id=layout.run_id,
            experiment_name=config.experiment.name,
            random_seed=config.random_seed,
            config_hash=config.config_hash(),
            split_hash=manifest["split_hash"],
            artifact_root_uri=layout.run_uri,
            resolved_config_uri=layout.resolved_config_uri(),
        )
        summary = parse_s3_uri(layout.run_summary_uri())
        store.put_text(
            summary.bucket,
            summary.key,
            run_summary_markdown(config=config, layout=layout, split_uri=layout.split_manifest_uri()),
            content_type="text/markdown; charset=utf-8",
        )
        return {"run_id": layout.run_id, "artifact_root": layout.run_uri, "split_manifest": layout.split_manifest_uri(), **manifest}
    finally:
        catalog.close()


def build_features(
    config: BenchmarkConfig,
    *,
    run_id: str,
    progress: ProgressReporter | None = None,
) -> list[dict[str, Any]]:
    layout = ArtifactLayout.from_config(config, run_id)
    catalog = BenchmarkCatalog.from_env(config.database)
    store = S3ArtifactStore.from_env(config.storage)
    try:
        catalog.ensure_schema()
        recordings = catalog.recordings_for_datasets(
            sorted(set(config.data.source_datasets) | set(config.data.target_datasets)),
            sample_rate_hz=config.data.sample_rate_hz,
        )
        recordings_by_dataset = _recordings_by_dataset(recordings)
        datasets = sorted(set(config.data.source_datasets) | set(config.data.target_datasets))
        cache_enabled = _reuse_enabled(config, "features")
        result_by_dataset: dict[str, dict[str, Any]] = {}
        recordings_to_build: list[Any] = []
        feature_roots: dict[str, str] = {}
        cache_metadata: dict[str, dict[str, Any]] = {}
        for dataset_name in datasets:
            dataset_recordings = recordings_by_dataset.get(dataset_name, [])
            cache_key = feature_cache_key(config, dataset_name, dataset_recordings)
            cache_uri = layout.feature_cache_dataset_uri(dataset_name, cache_key)
            cache_manifest_uri = layout.feature_cache_manifest_uri(dataset_name, cache_key)
            cached_manifest = _read_json_uri_if_exists(cache_manifest_uri, store) if cache_enabled else None
            reused = bool(
                cached_manifest
                and cached_manifest.get("cache_key") == cache_key
                and cached_manifest.get("dataset_name") == dataset_name
                and _feature_cache_is_complete(cached_manifest, store)
            )
            cache_metadata[dataset_name] = {
                "cache_key": cache_key,
                "cache_uri": cache_uri,
                "cache_manifest_uri": cache_manifest_uri,
                "cache_payload": feature_cache_payload(config, dataset_name, dataset_recordings),
                "enabled": cache_enabled,
            }
            if reused:
                stage_id = _progress_start(
                    progress,
                    "features",
                    f"Reuse feature windows for {dataset_name}",
                    dataset_name=dataset_name,
                    cache_key=cache_key,
                    cache_uri=cache_uri,
                )
                result = dict(cached_manifest)
                result["reused"] = True
                result_by_dataset[dataset_name] = result
                _progress_finish(
                    progress,
                    stage_id,
                    status="reused",
                    dataset_name=dataset_name,
                    cache_key=cache_key,
                    cache_uri=cache_uri,
                    labeled_windows=result.get("labeled_windows"),
                )
                continue
            if cache_enabled and config.reuse.mode == "require":
                raise RuntimeError(f"Required feature cache entry is missing for dataset '{dataset_name}'")
            feature_roots[dataset_name] = cache_uri if cache_enabled else layout.feature_dataset_uri(dataset_name)
            recordings_to_build.extend(dataset_recordings)

        built_results = (
            build_features_with_spark(
                config,
                layout,
                recordings_to_build,
                feature_roots=feature_roots,
                progress=progress,
            )
            if recordings_to_build
            else []
        )
        for result in built_results:
            metadata = cache_metadata[result.dataset_name]
            result_dict = asdict(result)
            result_dict.update(
                {
                    "cache_key": metadata["cache_key"],
                    "cache_payload": metadata["cache_payload"],
                    "cache_uri": metadata["cache_uri"],
                    "cache_manifest_uri": metadata["cache_manifest_uri"],
                    "reused": False,
                    "enabled": metadata["enabled"],
                    "schema_version": 1,
                }
            )
            if metadata["enabled"]:
                parsed_cache_manifest = parse_s3_uri(metadata["cache_manifest_uri"])
                store.put_json(
                    parsed_cache_manifest.bucket,
                    parsed_cache_manifest.key,
                    result_dict,
                    metadata={"cache-key": metadata["cache_key"], "cache-kind": "features"},
                )
            result_by_dataset[result.dataset_name] = result_dict

        result_dicts = [result_by_dataset[dataset_name] for dataset_name in datasets if dataset_name in result_by_dataset]
        manifest_uri = parse_s3_uri(layout.uri("features", "feature_manifest.json"))
        store.put_json(manifest_uri.bucket, manifest_uri.key, result_dicts)
        summary = parse_s3_uri(layout.run_summary_uri())
        store.put_text(
            summary.bucket,
            summary.key,
            run_summary_markdown(
                config=config,
                layout=layout,
                split_uri=layout.split_manifest_uri(),
                feature_uris=[str(result["feature_uri"]) for result in result_dicts if result.get("feature_uri")],
            ),
            content_type="text/markdown; charset=utf-8",
        )
        return result_dicts
    finally:
        catalog.close()


def train_model(
    config: BenchmarkConfig,
    *,
    run_id: str,
    source_dataset: str,
    model_id: str,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Train one configured baseline model and persist its artifact."""

    layout = ArtifactLayout.from_config(config, run_id)
    model_config = _model_config(config, model_id)
    with _configured_storage_env(config):
        split_index = _load_split_index(layout)
        model = create_model(model_id)
        train_rows = _rows_for_model_split(layout, source_dataset, split_index, "train", model.info.input_kind)
        val_rows = _rows_for_model_split(layout, source_dataset, split_index, "val", model.info.input_kind)
    if not train_rows and model.info.requires_training:
        raise RuntimeError(f"No training rows found for source dataset '{source_dataset}' and model '{model_id}'")

    params = dict(model_config.params)
    if "random_state" in model.info.configurable_parameters:
        params.setdefault("random_state", config.random_seed)

    stage_id = _progress_start(
        progress,
        "train",
        f"Train {model_id} on {source_dataset}",
        source_dataset=source_dataset,
        model_id=model_id,
        input_kind=model.info.input_kind,
        training_row_count=len(train_rows),
        validation_row_count=len(val_rows),
        requires_training=model.info.requires_training,
    )
    started_at = _utc_now()
    started_counter = time.perf_counter()
    with _configured_storage_env(config):
        model.fit(train_rows, val_rows, params)
        artifact_uri = layout.model_uri(source_dataset, model_id)
        model.save(artifact_uri)
    finished_at = _utc_now()
    wall_duration_seconds = time.perf_counter() - started_counter
    duration_seconds = wall_duration_seconds if model.info.requires_training else 0.0

    catalog = BenchmarkCatalog.from_env(config.database)
    try:
        catalog.ensure_schema()
        catalog.upsert_model(
            run_id=layout.run_id,
            source_dataset=source_dataset,
            model_id=model_id,
            artifact_uri=artifact_uri,
        )
    finally:
        catalog.close()
    store = S3ArtifactStore.from_env(config.storage)
    timing_row = _timing_row(
        layout.run_id,
        stage="train",
        source_dataset=source_dataset,
        target_dataset=None,
        model_id=model_id,
        input_kind=model.info.input_kind,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        row_count=len(train_rows),
        artifact_uri=artifact_uri,
        validation_row_count=len(val_rows),
        requires_training=model.info.requires_training,
        wall_duration_seconds=wall_duration_seconds,
    )
    _write_timings(layout, [timing_row], store)
    _progress_finish(
        progress,
        stage_id,
        status="succeeded",
        source_dataset=source_dataset,
        model_id=model_id,
        input_kind=model.info.input_kind,
        training_row_count=len(train_rows),
        validation_row_count=len(val_rows),
        duration_seconds=duration_seconds,
        wall_duration_seconds=wall_duration_seconds,
        artifact_uri=artifact_uri,
    )
    return {
        "run_id": layout.run_id,
        "source_dataset": source_dataset,
        "model_id": model_id,
        "artifact_uri": artifact_uri,
        "duration_seconds": duration_seconds,
    }


def train_all(
    config: BenchmarkConfig,
    *,
    run_id: str,
    progress: ProgressReporter | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_dataset in config.data.source_datasets:
        for model_config in config.models:
            results.append(
                train_model(
                    config,
                    run_id=run_id,
                    source_dataset=source_dataset,
                    model_id=model_config.id,
                    progress=progress,
                )
            )
    return results


def evaluate_run(
    config: BenchmarkConfig,
    *,
    run_id: str,
    progress: ProgressReporter | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all configured source-model artifacts on all target test splits."""

    layout = ArtifactLayout.from_config(config, run_id)
    with _configured_storage_env(config):
        split_index = _load_split_index(layout)
    store = S3ArtifactStore.from_env(config.storage)
    catalog = BenchmarkCatalog.from_env(config.database)
    metrics_rows: list[dict[str, Any]] = []
    timing_rows: list[dict[str, Any]] = []
    try:
        catalog.ensure_schema()
        for source_dataset in config.data.source_datasets:
            for model_config in config.models:
                with _configured_storage_env(config):
                    model = load_model(model_config.id, layout.model_uri(source_dataset, model_config.id))
                for target_dataset in config.data.target_datasets:
                    stage_id = _progress_start(
                        progress,
                        "evaluate",
                        f"Evaluate {model_config.id}: {source_dataset} to {target_dataset}",
                        source_dataset=source_dataset,
                        target_dataset=target_dataset,
                        model_id=model_config.id,
                        input_kind=model.info.input_kind,
                    )
                    evaluate_started_at = _utc_now()
                    evaluate_started_counter = time.perf_counter()
                    with _configured_storage_env(config):
                        target_rows = _rows_for_model_split(
                            layout,
                            target_dataset,
                            split_index,
                            "test",
                            model.info.input_kind,
                        )
                    if not target_rows:
                        _progress_finish(
                            progress,
                            stage_id,
                            status="skipped",
                            source_dataset=source_dataset,
                            target_dataset=target_dataset,
                            model_id=model_config.id,
                            row_count=0,
                            reason="No target test rows",
                        )
                        continue
                    full_target_row_count = len(target_rows)
                    max_eval_windows = _max_eval_windows(model_config.params)
                    target_rows = _limit_evaluation_rows(
                        target_rows,
                        max_eval_windows,
                        seed_parts=_evaluation_limit_seed_parts(
                            config,
                            target_dataset=target_dataset,
                            input_kind=model.info.input_kind,
                            limit=max_eval_windows,
                        ),
                    )
                    inference_started_at = _utc_now()
                    inference_started_counter = time.perf_counter()
                    with _configured_storage_env(config):
                        scores = model.predict_proba(target_rows)
                    inference_finished_at = _utc_now()
                    inference_duration_seconds = time.perf_counter() - inference_started_counter
                    window_rows = [
                        {
                            "source_dataset": source_dataset,
                            "target_dataset": target_dataset,
                            "model_id": model_config.id,
                            "full_recording_id": row["full_recording_id"],
                            "window_index": row.get("window_index"),
                            "window_start_seconds": row.get("window_start_seconds"),
                            "window_end_seconds": row.get("window_end_seconds"),
                            "label": int(row["label"]),
                            "af_probability": float(score),
                        }
                        for row, score in zip(target_rows, scores)
                    ]
                    window_uri = layout.window_predictions_uri(source_dataset, target_dataset, model_config.id)
                    timing_rows.append(
                        _timing_row(
                            layout.run_id,
                            stage="inference",
                            source_dataset=source_dataset,
                            target_dataset=target_dataset,
                            model_id=model_config.id,
                            input_kind=model.info.input_kind,
                            started_at=inference_started_at,
                            finished_at=inference_finished_at,
                            duration_seconds=inference_duration_seconds,
                            row_count=len(target_rows),
                            artifact_uri=window_uri,
                        )
                    )
                    _write_parquet_rows(window_rows, window_uri, store)

                    window_predictions = [
                        BinaryPrediction(
                            y_true=int(row["label"]),
                            y_score=float(score),
                            recording_id=str(row["full_recording_id"]),
                        )
                        for row, score in zip(target_rows, scores)
                    ]
                    window_metrics = compute_binary_metrics(window_predictions, metric_names=config.metrics)
                    catalog.upsert_metrics(
                        run_id=layout.run_id,
                        source_dataset=source_dataset,
                        target_dataset=target_dataset,
                        model_id=model_config.id,
                        evaluation_level=WINDOW_LEVEL,
                        metrics=window_metrics,
                        artifact_uri=layout.evaluation_metrics_json_uri(WINDOW_LEVEL),
                    )
                    metrics_rows.extend(
                        _metric_rows(
                            layout.run_id,
                            source_dataset,
                            target_dataset,
                            model_config.id,
                            WINDOW_LEVEL,
                            window_metrics,
                        )
                    )

                    recording_predictions = aggregate_by_recording(
                        window_predictions,
                        policy=config.data.recording_aggregation,
                        label_policy=config.data.recording_label_policy,
                        label_threshold=config.data.recording_label_threshold,
                    )
                    recording_rows = [
                        {
                            "source_dataset": source_dataset,
                            "target_dataset": target_dataset,
                            "model_id": model_config.id,
                            "full_recording_id": item.recording_id,
                            "label": int(item.y_true),
                            "af_probability": float(item.y_score),
                        }
                        for item in recording_predictions
                    ]
                    recording_uri = layout.recording_predictions_uri(source_dataset, target_dataset, model_config.id)
                    _write_parquet_rows(recording_rows, recording_uri, store)

                    recording_metrics = compute_binary_metrics(recording_predictions, metric_names=config.metrics)
                    catalog.upsert_metrics(
                        run_id=layout.run_id,
                        source_dataset=source_dataset,
                        target_dataset=target_dataset,
                        model_id=model_config.id,
                        evaluation_level=RECORDING_LEVEL,
                        metrics=recording_metrics,
                        artifact_uri=layout.evaluation_metrics_json_uri(RECORDING_LEVEL),
                    )
                    metrics_rows.extend(
                        _metric_rows(
                            layout.run_id,
                            source_dataset,
                            target_dataset,
                            model_config.id,
                            RECORDING_LEVEL,
                            recording_metrics,
                        )
                    )
                    evaluate_finished_at = _utc_now()
                    timing_rows.append(
                        _timing_row(
                            layout.run_id,
                            stage="evaluate",
                            source_dataset=source_dataset,
                            target_dataset=target_dataset,
                            model_id=model_config.id,
                            input_kind=model.info.input_kind,
                            started_at=evaluate_started_at,
                            finished_at=evaluate_finished_at,
                            duration_seconds=time.perf_counter() - evaluate_started_counter,
                            row_count=len(target_rows),
                            artifact_uri=layout.evaluation_metrics_json_uri(WINDOW_LEVEL),
                        )
                    )
                    _progress_finish(
                        progress,
                        stage_id,
                        status="succeeded",
                        source_dataset=source_dataset,
                        target_dataset=target_dataset,
                        model_id=model_config.id,
                        input_kind=model.info.input_kind,
                        row_count=len(target_rows),
                        full_row_count=full_target_row_count,
                        max_eval_windows=max_eval_windows,
                        duration_seconds=time.perf_counter() - evaluate_started_counter,
                        window_metrics=window_metrics,
                        recording_metrics=recording_metrics,
                    )

        _write_metrics(layout, metrics_rows, store)
        _write_timings(layout, timing_rows, store)
        return metrics_rows
    finally:
        catalog.close()


def write_matrix(
    run_id: str,
    *,
    config: BenchmarkConfig | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, str]:
    """Write wide benchmark matrix CSV files for each evaluation level."""

    layout = ArtifactLayout.from_config(config, run_id) if config else _layout_for_existing_run(run_id)
    store = S3ArtifactStore.from_env(config.storage) if config else S3ArtifactStore.from_env()
    matrix_uris: dict[str, str] = {}
    for evaluation_level in EVALUATION_LEVELS:
        stage_id = _progress_start(
            progress,
            "matrix",
            f"Write {evaluation_level} benchmark matrix",
            evaluation_level=evaluation_level,
        )
        metric_rows = _read_metrics_for_level(layout, store, evaluation_level)
        if not metric_rows:
            _progress_finish(
                progress,
                stage_id,
                status="skipped",
                evaluation_level=evaluation_level,
                reason="No metric rows",
            )
            continue
        matrix_rows = _metrics_matrix_rows(metric_rows)
        matrix_uri = layout.evaluation_matrix_csv_uri(evaluation_level)
        _write_csv_uri(matrix_rows, matrix_uri, store)
        matrix_uris[evaluation_level] = matrix_uri
        if evaluation_level == WINDOW_LEVEL:
            _write_csv_uri(matrix_rows, layout.matrix_csv_uri(), store)
        _progress_finish(
            progress,
            stage_id,
            status="succeeded",
            evaluation_level=evaluation_level,
            row_count=len(matrix_rows),
            artifact_uri=matrix_uri,
        )
    return matrix_uris


def run_full_workflow(
    config: BenchmarkConfig,
    *,
    run_id: str | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    _progress_event(
        progress,
        "workflow_started",
        experiment_name=config.experiment.name,
        config_hash=config.config_hash(),
    )
    if progress:
        progress.plan(
            total=_planned_stage_count(config),
            label="Benchmark workflow",
            source_datasets=config.data.source_datasets,
            target_datasets=config.data.target_datasets,
            model_count=len(config.models),
        )
    try:
        split_result = make_splits(config, run_id=run_id, progress=progress)
        resolved_run_id = str(split_result["run_id"])
        _progress_event(
            progress,
            "run_resolved",
            run_id=resolved_run_id,
            artifact_root=split_result["artifact_root"],
            split_manifest=split_result["split_manifest"],
        )
        feature_results = build_features(config, run_id=resolved_run_id, progress=progress)
        train_results = train_all(config, run_id=resolved_run_id, progress=progress)
        metric_rows = evaluate_run(config, run_id=resolved_run_id, progress=progress)
        matrix_uris = write_matrix(resolved_run_id, config=config, progress=progress)
        result = {
            "run_id": resolved_run_id,
            "artifact_root": split_result["artifact_root"],
            "split_manifest": split_result["split_manifest"],
            "features": feature_results,
            "models": train_results,
            "metrics": metric_rows,
            "matrices": matrix_uris,
        }
        _progress_event(
            progress,
            "workflow_finished",
            run_id=resolved_run_id,
            artifact_root=split_result["artifact_root"],
            metric_rows=len(metric_rows),
        )
        return result
    except Exception as exc:
        _progress_event(progress, "workflow_failed", error=str(exc))
        raise


def load_metrics_payload(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _model_config(config: BenchmarkConfig, model_id: str):
    for item in config.models:
        if item.id == model_id:
            return item
    raise ConfigError(f"Model '{model_id}' is not present in the config")


def _planned_stage_count(config: BenchmarkConfig) -> int:
    dataset_count = len(set(config.data.source_datasets) | set(config.data.target_datasets))
    model_count = len(config.models)
    train_count = len(config.data.source_datasets) * model_count
    evaluate_count = len(config.data.source_datasets) * model_count * len(config.data.target_datasets)
    matrix_count = len(EVALUATION_LEVELS)
    return dataset_count + dataset_count + train_count + evaluate_count + matrix_count


def _progress_event(progress: ProgressReporter | None, event: str, **payload: Any) -> None:
    if progress is not None:
        progress.emit(event, **payload)


def _progress_start(progress: ProgressReporter | None, stage: str, label: str, **payload: Any) -> str | None:
    return progress.start(stage, label, **payload) if progress is not None else None


def _progress_finish(
    progress: ProgressReporter | None,
    stage_id: str | None,
    *,
    status: str = "succeeded",
    **payload: Any,
) -> None:
    if progress is not None and stage_id is not None:
        progress.finish(stage_id, status=status, **payload)


@contextmanager
def _configured_storage_env(config: BenchmarkConfig):
    """Expose configured S3 env names to model/helper code that uses defaults.

    Most workflow-level storage access passes ``config.storage`` directly. Model
    adapters and lower-level parquet helpers intentionally keep a simple
    ``S3ArtifactStore.from_env()`` API, so this context mirrors custom env names
    onto the canonical names for the duration of a read/write operation.
    """

    mapping = {
        "S3_ENDPOINT_URL": config.storage.endpoint_env,
        "S3_ACCESS_KEY": config.storage.access_key_env,
        "S3_SECRET_KEY": config.storage.secret_key_env,
    }
    previous = {name: os.environ.get(name) for name in mapping}
    try:
        for canonical, configured in mapping.items():
            if configured == canonical:
                continue
            value = os.environ.get(configured)
            if value is None:
                os.environ.pop(canonical, None)
            else:
                os.environ[canonical] = value
        yield
    finally:
        for canonical, value in previous.items():
            if value is None:
                os.environ.pop(canonical, None)
            else:
                os.environ[canonical] = value


def _load_split_index(layout: ArtifactLayout) -> dict[str, str]:
    store = S3ArtifactStore.from_env()
    manifest = _read_json_uri(layout.split_manifest_uri(), store)
    return {
        str(item["full_recording_id"]): str(item["split"])
        for item in manifest.get("assignments", [])
    }


def _feature_rows_for_split(
    layout: ArtifactLayout,
    dataset_name: str,
    split_index: dict[str, str],
    split_name: str,
) -> list[dict[str, Any]]:
    rows = _read_parquet_rows(_feature_windows_uri_for_dataset(layout, dataset_name))
    return _filter_rows_for_split(rows, split_index, split_name)


def _waveform_rows_for_split(
    layout: ArtifactLayout,
    dataset_name: str,
    split_index: dict[str, str],
    split_name: str,
) -> list[dict[str, Any]]:
    rows = _read_parquet_rows(_waveform_windows_uri_for_dataset(layout, dataset_name))
    return _filter_rows_for_split(rows, split_index, split_name)


def _rows_for_model_split(
    layout: ArtifactLayout,
    dataset_name: str,
    split_index: dict[str, str],
    split_name: str,
    input_kind: str,
) -> list[dict[str, Any]]:
    if input_kind == "tabular":
        return _feature_rows_for_split(layout, dataset_name, split_index, split_name)
    if input_kind == "waveform":
        return _waveform_rows_for_split(layout, dataset_name, split_index, split_name)
    raise RuntimeError(f"Unsupported model input kind: {input_kind}")


def _filter_rows_for_split(
    rows: list[dict[str, Any]],
    split_index: dict[str, str],
    split_name: str,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        recording_id = str(row.get("full_recording_id"))
        if split_index.get(recording_id) == split_name:
            row["split"] = split_name
            filtered.append(row)
    return filtered


def _reuse_enabled(config: BenchmarkConfig, kind: str) -> bool:
    if not config.reuse.enabled or config.reuse.mode == "off":
        return False
    if kind == "splits":
        return config.reuse.splits
    if kind == "features":
        return config.reuse.features
    raise RuntimeError(f"Unknown reuse kind: {kind}")


def _split_ratios(config: BenchmarkConfig) -> dict[str, float]:
    return {
        "train": config.data.split.train,
        "val": config.data.split.val,
        "test": config.data.split.test,
    }


def _recordings_by_dataset(recordings: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for record in recordings:
        grouped.setdefault(str(record.dataset_name), []).append(record)
    return grouped


def _split_assignment_from_dict(item: dict[str, Any]) -> SplitAssignment:
    return SplitAssignment(
        dataset_name=str(item["dataset_name"]),
        full_recording_id=str(item["full_recording_id"]),
        split=str(item["split"]),
        group_id=str(item["group_id"]),
        af_present=item.get("af_present"),
    )


def _read_json_uri_if_exists(uri: str, store: S3ArtifactStore) -> dict[str, Any] | None:
    parsed = parse_s3_uri(uri)
    try:
        if not store.prefix_exists(parsed.bucket, parsed.key):
            return None
        return _read_json_uri(uri, store)
    except Exception:
        return None


def _prefix_exists_uri(uri: str, store: S3ArtifactStore) -> bool:
    parsed = parse_s3_uri(uri)
    return store.prefix_exists(parsed.bucket, parsed.key.rstrip("/"))


def _feature_cache_is_complete(manifest: dict[str, Any], store: S3ArtifactStore) -> bool:
    labeled_windows = int(manifest.get("labeled_windows") or 0)
    if labeled_windows <= 0:
        return True
    windows_uri = manifest.get("windows_uri") or _uri_under_feature_root(manifest, "windows.parquet")
    waveform_uri = manifest.get("waveform_uri") or _uri_under_feature_root(manifest, "waveform_windows.parquet")
    return bool(windows_uri and waveform_uri and _prefix_exists_uri(str(windows_uri), store) and _prefix_exists_uri(str(waveform_uri), store))


def _feature_manifest_entries(layout: ArtifactLayout) -> list[dict[str, Any]]:
    store = S3ArtifactStore.from_env()
    manifest = _read_json_uri_if_exists(layout.uri("features", "feature_manifest.json"), store)
    if manifest is None:
        return []
    if isinstance(manifest, list):
        return [item for item in manifest if isinstance(item, dict)]
    datasets = manifest.get("datasets") if isinstance(manifest, dict) else None
    return [item for item in datasets if isinstance(item, dict)] if isinstance(datasets, list) else []


def _feature_entry_for_dataset(layout: ArtifactLayout, dataset_name: str) -> dict[str, Any] | None:
    for entry in _feature_manifest_entries(layout):
        if str(entry.get("dataset_name")) == dataset_name:
            return entry
    return None


def _feature_windows_uri_for_dataset(layout: ArtifactLayout, dataset_name: str) -> str:
    entry = _feature_entry_for_dataset(layout, dataset_name)
    if entry is None:
        return layout.feature_windows_uri(dataset_name)
    return str(entry.get("windows_uri") or _uri_under_feature_root(entry, "windows.parquet") or layout.feature_windows_uri(dataset_name))


def _waveform_windows_uri_for_dataset(layout: ArtifactLayout, dataset_name: str) -> str:
    entry = _feature_entry_for_dataset(layout, dataset_name)
    if entry is None:
        return layout.waveform_windows_uri(dataset_name)
    return str(
        entry.get("waveform_uri")
        or _uri_under_feature_root(entry, "waveform_windows.parquet")
        or layout.waveform_windows_uri(dataset_name)
    )


def _uri_under_feature_root(entry: dict[str, Any], *parts: str) -> str | None:
    feature_uri = entry.get("feature_uri") or entry.get("cache_uri")
    if not feature_uri:
        return None
    return "/".join([str(feature_uri).rstrip("/"), *[part.strip("/") for part in parts if part.strip("/")]])


def _read_json_uri(uri: str, store: S3ArtifactStore) -> dict[str, Any]:
    parsed = parse_s3_uri(uri)
    return json.loads(store.get_bytes(parsed.bucket, parsed.key).decode("utf-8"))


def _metric_rows(
    run_id: str,
    source_dataset: str,
    target_dataset: str,
    model_id: str,
    evaluation_level: str,
    metrics: dict[str, float | None],
) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "source_dataset": source_dataset,
            "target_dataset": target_dataset,
            "model_id": model_id,
            "evaluation_level": evaluation_level,
            "metric_name": metric_name,
            "metric_value": metric_value,
        }
        for metric_name, metric_value in metrics.items()
    ]


def _max_eval_windows(params: dict[str, Any]) -> int | None:
    value = params.get("max_eval_windows")
    if value is None:
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_eval_windows must be a positive integer or null") from exc
    if limit <= 0:
        raise ValueError("max_eval_windows must be a positive integer or null")
    return limit


def _evaluation_limit_seed_parts(
    config: BenchmarkConfig,
    *,
    target_dataset: str,
    input_kind: str,
    limit: int | None,
) -> list[str]:
    limit_part = "all" if limit is None else str(limit)
    return [str(config.random_seed), str(target_dataset), str(input_kind), limit_part]


def _limit_evaluation_rows(
    rows: list[dict[str, Any]],
    limit: int | None,
    *,
    seed_parts: list[str],
) -> list[dict[str, Any]]:
    if limit is None or len(rows) <= limit:
        return rows
    seed_prefix = "|".join(seed_parts)
    ranked = sorted(
        enumerate(rows),
        key=lambda item: _stable_row_key(seed_prefix, item[1], item[0]),
    )
    selected_indices = sorted(index for index, _ in ranked[:limit])
    return [rows[index] for index in selected_indices]


def _stable_row_key(seed_prefix: str, row: dict[str, Any], index: int) -> str:
    identity = "|".join(
        [
            seed_prefix,
            str(row.get("full_recording_id", "")),
            str(row.get("window_index", "")),
            str(row.get("window_start_seconds", "")),
            str(index),
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _timing_row(
    run_id: str,
    *,
    stage: str,
    source_dataset: str | None,
    target_dataset: str | None,
    model_id: str | None,
    input_kind: str | None,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    duration_seconds: float,
    row_count: int | None = None,
    artifact_uri: str | None = None,
    validation_row_count: int | None = None,
    requires_training: bool | None = None,
    wall_duration_seconds: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "source_dataset": source_dataset,
        "target_dataset": target_dataset,
        "model_id": model_id,
        "input_kind": input_kind,
        "started_at": _isoformat(started_at),
        "finished_at": _isoformat(finished_at),
        "duration_seconds": round(max(0.0, float(duration_seconds)), 6),
    }
    if row_count is not None:
        row["row_count"] = int(row_count)
    if validation_row_count is not None:
        row["validation_row_count"] = int(validation_row_count)
    if requires_training is not None:
        row["requires_training"] = bool(requires_training)
    if wall_duration_seconds is not None:
        row["wall_duration_seconds"] = round(max(0.0, float(wall_duration_seconds)), 6)
    if artifact_uri:
        row["artifact_uri"] = artifact_uri
    return row


def _write_timings(layout: ArtifactLayout, timing_rows: list[dict[str, Any]], store: S3ArtifactStore) -> None:
    if not timing_rows:
        return
    rows = _merge_timing_rows(_read_timings(layout, store), timing_rows)
    payload = {"run_id": layout.run_id, "timings": rows}
    timings_json = parse_s3_uri(layout.timings_json_uri())
    timings_csv = parse_s3_uri(layout.timings_csv_uri())
    store.put_json(timings_json.bucket, timings_json.key, payload)
    store.put_text(
        timings_csv.bucket,
        timings_csv.key,
        metrics_to_csv(rows),
        content_type="text/csv; charset=utf-8",
    )


def _read_timings(layout: ArtifactLayout, store: S3ArtifactStore) -> list[dict[str, Any]]:
    uri = layout.timings_json_uri()
    parsed = parse_s3_uri(uri)
    try:
        if not store.prefix_exists(parsed.bucket, parsed.key):
            return []
        payload = _read_json_uri(uri, store)
    except Exception:
        return []
    rows = payload.get("timings", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _merge_timing_rows(existing: list[dict[str, Any]], updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in [*existing, *updates]:
        key = (
            str(row.get("run_id") or ""),
            str(row.get("stage") or ""),
            str(row.get("source_dataset") or ""),
            str(row.get("target_dataset") or ""),
            str(row.get("model_id") or ""),
        )
        merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _isoformat(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _write_metrics(layout: ArtifactLayout, metrics_rows: list[dict[str, Any]], store: S3ArtifactStore) -> None:
    rows_by_level = {
        level: [row for row in metrics_rows if row.get("evaluation_level") == level]
        for level in EVALUATION_LEVELS
    }
    for evaluation_level, rows in rows_by_level.items():
        if not rows:
            continue
        payload = {"run_id": layout.run_id, "evaluation_level": evaluation_level, "metrics": rows}
        metrics_json = parse_s3_uri(layout.evaluation_metrics_json_uri(evaluation_level))
        metrics_csv = parse_s3_uri(layout.evaluation_metrics_csv_uri(evaluation_level))
        store.put_json(metrics_json.bucket, metrics_json.key, payload)
        store.put_text(
            metrics_csv.bucket,
            metrics_csv.key,
            metrics_to_csv(rows),
            content_type="text/csv; charset=utf-8",
        )

    primary_rows = rows_by_level.get(WINDOW_LEVEL) or metrics_rows
    legacy_payload = {"run_id": layout.run_id, "evaluation_level": WINDOW_LEVEL, "metrics": primary_rows}
    legacy_json = parse_s3_uri(layout.metrics_json_uri())
    legacy_csv = parse_s3_uri(layout.metrics_csv_uri())
    store.put_json(legacy_json.bucket, legacy_json.key, legacy_payload)
    store.put_text(
        legacy_csv.bucket,
        legacy_csv.key,
        metrics_to_csv(primary_rows),
        content_type="text/csv; charset=utf-8",
    )


def _read_metrics_for_level(
    layout: ArtifactLayout,
    store: S3ArtifactStore,
    evaluation_level: str,
) -> list[dict[str, Any]]:
    uri = layout.evaluation_metrics_json_uri(evaluation_level)
    parsed = parse_s3_uri(uri)
    if store.prefix_exists(parsed.bucket, parsed.key):
        payload = _read_json_uri(uri, store)
        return list(payload.get("metrics", []))
    if evaluation_level == WINDOW_LEVEL:
        payload = _read_json_uri(layout.metrics_json_uri(), store)
        return list(payload.get("metrics", []))
    return []


def _metrics_matrix_rows(metric_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in metric_rows:
        key = (row["source_dataset"], row["target_dataset"], row["model_id"])
        grouped.setdefault(
            key,
            {
                "source_dataset": row["source_dataset"],
                "target_dataset": row["target_dataset"],
                "model_id": row["model_id"],
            },
        )[row["metric_name"]] = row["metric_value"]
    return [grouped[key] for key in sorted(grouped)]


def _write_csv_uri(rows: list[dict[str, Any]], uri: str, store: S3ArtifactStore) -> None:
    parsed = parse_s3_uri(uri)
    store.put_text(
        parsed.bucket,
        parsed.key,
        metrics_to_csv(rows),
        content_type="text/csv; charset=utf-8",
    )


def _read_parquet_rows(uri: str) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas and pyarrow are required to read feature parquet artifacts") from exc

    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(uri)
    with tempfile.TemporaryDirectory(prefix="ecg-bench-read-") as tmpdir:
        root = Path(tmpdir) / "dataset"
        keys = [key for key in store.iter_keys(parsed.bucket, parsed.key.rstrip("/")) if key.endswith(".parquet")]
        if not keys:
            return []
        for key in keys:
            rel = Path(key).relative_to(parsed.key.rstrip("/"))
            store.download_file(parsed.bucket, key, root / rel)
        frame = pd.read_parquet(root)
        frame = frame.where(pd.notnull(frame), None)
        return frame.to_dict(orient="records")


def _write_parquet_rows(rows: list[dict[str, Any]], uri: str, store: S3ArtifactStore) -> None:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas and pyarrow are required to write parquet artifacts") from exc

    parsed = parse_s3_uri(uri)
    with tempfile.TemporaryDirectory(prefix="ecg-bench-write-") as tmpdir:
        path = Path(tmpdir) / "part-00000.parquet"
        pd.DataFrame(rows).to_parquet(path, index=False)
        store.upload_file(parsed.bucket, parsed.key, path)


def _layout_for_existing_run(run_id: str) -> ArtifactLayout:
    catalog = BenchmarkCatalog.from_env()
    try:
        row = catalog.get_run(run_id)
    finally:
        catalog.close()
    if row is None:
        raise RuntimeError(f"Run '{run_id}' was not found in benchmark_runs")
    root_uri = str(row["artifact_root_uri"]).rstrip("/")
    parsed = parse_s3_uri(root_uri + "/_root")
    root_key = parsed.key.removesuffix("/_root")
    suffix = f"/{run_id}"
    if not root_key.endswith(suffix):
        raise RuntimeError(f"Run artifact root does not end with run id '{run_id}': {root_uri}")
    prefix = root_key[: -len(suffix)]
    return ArtifactLayout(bucket=parsed.bucket, prefix=prefix, run_id=run_id)
