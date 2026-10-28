from __future__ import annotations

import math
import os
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ecg_pipeline.annotations import AnnotationContext, build_annotation_adapter
from ecg_pipeline.annotations.base import AnnotationDrop
from ecg_pipeline.config import EnvConfig, PipelineConfig
from ecg_pipeline.manifest import DatasetManifest
from ecg_pipeline.reader.dispatcher import FormatDispatcher
from ecg_pipeline.reader.models import (
    ECGAnnotations,
    ProcessingFailure,
    ProcessingResult,
    RecordingFileGroup,
)
from ecg_pipeline.reader.readers import CSVReader, HDF5Reader, MATReader, WFDBReader
from ecg_pipeline.reader.resampler import adjust_annotations, resample_signal
from ecg_pipeline.reader.writers import write_annotations_json, write_parquet
from ecg_pipeline.utils.s3 import S3Client


def process_dataset(
    *,
    manifest: DatasetManifest,
    s3_client: S3Client,
    config: PipelineConfig,
    target_rate: int | None = None,
    env: EnvConfig | None = None,
    spark_master: str | None = None,
) -> tuple[list[ProcessingResult], list[ProcessingFailure], str]:
    dataset_name = manifest.dataset.name
    bucket = config.s3_bucket
    raw_prefix = f"raw/{dataset_name}/"
    desired_rate = int(target_rate or config.default_target_sample_rate)
    processed_prefix = f"processed/{desired_rate}hz/{dataset_name}/"

    keys = [k for k in s3_client.iter_keys(bucket, raw_prefix) if _is_data_key(k)]
    rel_files = [k[len(raw_prefix) :] for k in keys if k.startswith(raw_prefix)]
    if not rel_files:
        raise ValueError(f"No raw files found under s3://{bucket}/{raw_prefix}")

    annotation_adapter = build_annotation_adapter(
        manifest.annotations.adapter,
        options=manifest.annotations.options,
    )
    annotation_context = AnnotationContext(
        manifest=manifest,
        dataset_name=dataset_name,
        bucket=bucket,
        raw_prefix=raw_prefix,
        raw_files=rel_files,
        s3_client=s3_client,
    )
    annotation_adapter.prepare(annotation_context)

    sidecar_files = annotation_adapter.sidecar_files(annotation_context)
    signal_files = [path for path in rel_files if path not in sidecar_files]
    if not signal_files:
        raise ValueError("No signal files discovered after applying annotation sidecar exclusions")

    dispatcher = FormatDispatcher(readers=[WFDBReader(), CSVReader(), MATReader(), HDF5Reader()])
    groups = dispatcher.discover_and_group(signal_files)
    if not groups:
        raise ValueError(
            "No processable recordings discovered. Supported formats: WFDB, CSV, MAT, HDF5."
        )

    resolved_master = _resolve_spark_master(spark_master=spark_master, env=env)
    use_spark = len(groups) >= int(config.min_recordings_for_spark)
    if use_spark:
        results, failures, dropped_stats = _process_groups_via_spark(
            groups=groups,
            manifest=manifest,
            s3_client=s3_client,
            config=config,
            dataset_name=dataset_name,
            bucket=bucket,
            raw_prefix=raw_prefix,
            processed_prefix=processed_prefix,
            desired_rate=desired_rate,
            raw_files=rel_files,
            spark_master=resolved_master,
        )
    else:
        results, failures, dropped_stats = _process_groups_sequential(
            groups=groups,
            dispatcher=dispatcher,
            dataset_name=dataset_name,
            bucket=bucket,
            raw_prefix=raw_prefix,
            processed_prefix=processed_prefix,
            desired_rate=desired_rate,
            parquet_compression=config.parquet_compression,
            s3_client=s3_client,
            annotation_adapter=annotation_adapter,
            annotation_context=annotation_context,
        )

    report_uri = write_processing_report(
        dataset_name=dataset_name,
        bucket=bucket,
        processed_prefix=processed_prefix,
        target_rate=desired_rate,
        total_recordings=len(results) + len(failures) + int(dropped_stats["total_recordings"]),
        results=results,
        failures=failures,
        dropped_stats=dropped_stats,
        s3_client=s3_client,
    )
    return results, failures, report_uri


def _resolve_spark_master(*, spark_master: str | None, env: EnvConfig | None) -> str:
    if spark_master and spark_master.strip():
        return spark_master.strip()
    if env is not None and env.spark_master and env.spark_master.strip():
        return env.spark_master.strip()
    return (os.environ.get("SPARK_MASTER") or "local[*]").strip()


def _process_groups_via_spark(
    *,
    groups: list[RecordingFileGroup],
    manifest: DatasetManifest,
    s3_client: S3Client,
    config: PipelineConfig,
    dataset_name: str,
    bucket: str,
    raw_prefix: str,
    processed_prefix: str,
    desired_rate: int,
    raw_files: list[str],
    spark_master: str,
) -> tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]:
    from ecg_pipeline.reader.spark_processor import SparkProcessJob, process_groups_with_spark

    job = SparkProcessJob(
        dataset_name=dataset_name,
        bucket=bucket,
        raw_prefix=raw_prefix,
        processed_prefix=processed_prefix,
        desired_rate=desired_rate,
        parquet_compression=config.parquet_compression,
        adapter_name=manifest.annotations.adapter,
        adapter_options=dict(manifest.annotations.options),
        raw_files=tuple(raw_files),
        manifest=manifest,
        s3_endpoint_url=s3_client.endpoint_url,
        s3_access_key=s3_client.access_key,
        s3_secret_key=s3_client.secret_key,
        s3_region_name=s3_client.region_name,
    )
    try:
        return process_groups_with_spark(
            groups=groups,
            job=job,
            config=config,
            spark_master=spark_master,
        )
    except Exception as exc:
        warnings.warn(
            (
                f"Spark processing unavailable for {dataset_name} "
                f"({len(groups)} groups); falling back to sequential processing: {exc}"
            ),
            RuntimeWarning,
            stacklevel=2,
        )
        print(
            f"WARNING: Spark fallback for {dataset_name}: {exc}",
            file=sys.stderr,
        )
        annotation_adapter = build_annotation_adapter(
            manifest.annotations.adapter,
            options=manifest.annotations.options,
        )
        annotation_context = AnnotationContext(
            manifest=manifest,
            dataset_name=dataset_name,
            bucket=bucket,
            raw_prefix=raw_prefix,
            raw_files=list(raw_files),
            s3_client=s3_client,
        )
        annotation_adapter.prepare(annotation_context)
        dispatcher = FormatDispatcher(readers=[WFDBReader(), CSVReader(), MATReader(), HDF5Reader()])
        return _process_groups_sequential(
            groups=groups,
            dispatcher=dispatcher,
            dataset_name=dataset_name,
            bucket=bucket,
            raw_prefix=raw_prefix,
            processed_prefix=processed_prefix,
            desired_rate=desired_rate,
            parquet_compression=config.parquet_compression,
            s3_client=s3_client,
            annotation_adapter=annotation_adapter,
            annotation_context=annotation_context,
        )


def _process_groups_sequential(
    *,
    groups: list[RecordingFileGroup],
    dispatcher: FormatDispatcher,
    dataset_name: str,
    bucket: str,
    raw_prefix: str,
    processed_prefix: str,
    desired_rate: int,
    parquet_compression: str,
    s3_client: S3Client,
    annotation_adapter,
    annotation_context: AnnotationContext,
) -> tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]:
    results: list[ProcessingResult] = []
    failures: list[ProcessingFailure] = []
    dropped_stats = _new_dropped_stats()

    for group in groups:
        reader = dispatcher.get_reader_for_group(group)
        group_results, group_failures, group_dropped_stats = process_recording_group(
            group=group,
            dataset_name=dataset_name,
            bucket=bucket,
            raw_prefix=raw_prefix,
            processed_prefix=processed_prefix,
            desired_rate=desired_rate,
            parquet_compression=parquet_compression,
            s3_client=s3_client,
            reader=reader,
            annotation_adapter=annotation_adapter,
            annotation_context=annotation_context,
        )
        results.extend(group_results)
        failures.extend(group_failures)
        _merge_dropped_stats(dropped_stats, group_dropped_stats)
    return results, failures, dropped_stats


def process_recording_group(
    *,
    group: RecordingFileGroup,
    dataset_name: str,
    bucket: str,
    raw_prefix: str,
    processed_prefix: str,
    desired_rate: int,
    parquet_compression: str,
    s3_client: S3Client,
    reader,
    annotation_adapter,
    annotation_context: AnnotationContext,
) -> tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]:
    results: list[ProcessingResult] = []
    failures: list[ProcessingFailure] = []
    dropped_stats = _new_dropped_stats()

    with TemporaryDirectory(prefix="ecg-reader-") as tmp:
        tmp_dir = Path(tmp)
        try:
            for rel in group.associated_files:
                s3_client.download_file(
                    bucket=bucket,
                    key=f"{raw_prefix}{rel}",
                    filename=tmp_dir / rel,
                )

            reader_options = annotation_adapter.reader_options_for_group(file_group=group)
            recordings = reader.read_local_many(
                file_group=group,
                local_dataset_dir=tmp_dir,
                dataset_name=dataset_name,
                reader_options=reader_options,
            )
            if not recordings:
                raise ValueError(f"Reader returned no recordings for file group: {group.primary_file}")
        except Exception as exc:
            failures.append(
                ProcessingFailure(
                    recording_name=group.recording_base_name,
                    primary_file=group.primary_file,
                    error=str(exc),
                )
            )
            return results, failures, dropped_stats

        for recording_index, recording in enumerate(recordings):
            try:
                annotation_adapter.enrich_recording(
                    recording,
                    context=annotation_context,
                    file_group=group,
                    recording_index=recording_index,
                )

                original_rate = int(recording.sampling_rate)
                if original_rate != desired_rate:
                    recording.signal = resample_signal(recording.signal, original_rate, desired_rate)
                    recording.num_samples = int(recording.signal.shape[0])
                    recording.duration_seconds = (
                        float(recording.num_samples) / float(desired_rate)
                        if desired_rate > 0
                        else recording.duration_seconds
                    )
                    if recording.annotations is not None:
                        recording.annotations = adjust_annotations(recording.annotations, original_rate, desired_rate)

                parquet_path = write_parquet(
                    recording=recording,
                    dataset_name=dataset_name,
                    bucket=bucket,
                    processed_prefix=processed_prefix,
                    target_rate=desired_rate,
                    original_rate=original_rate,
                    parquet_compression=parquet_compression,
                    s3_client=s3_client,
                )

                annotations_path = None
                if recording.annotations is not None or recording.labels:
                    annotations_path = write_annotations_json(
                        recording=recording,
                        bucket=bucket,
                        processed_prefix=processed_prefix,
                        target_rate=desired_rate,
                        original_rate=original_rate,
                        s3_client=s3_client,
                    )

                results.append(
                    _build_processing_result(
                        recording=recording,
                        parquet_path=parquet_path,
                        annotations_path=annotations_path,
                        sampling_rate=desired_rate,
                        original_sampling_rate=original_rate,
                    )
                )
            except AnnotationDrop as drop_exc:
                _record_drop(
                    dropped_stats,
                    reason=drop_exc.reason,
                    dropped_rows=drop_exc.dropped_rows,
                    dropped_recordings=drop_exc.dropped_recordings,
                )
            except Exception as exc:
                failures.append(
                    ProcessingFailure(
                        recording_name=recording.recording_name,
                        primary_file=group.primary_file,
                        error=str(exc),
                    )
                )

    return results, failures, dropped_stats


def write_processing_report(
    *,
    dataset_name: str,
    bucket: str,
    processed_prefix: str,
    target_rate: int,
    total_recordings: int,
    results: list[ProcessingResult],
    failures: list[ProcessingFailure],
    dropped_stats: dict[str, Any],
    s3_client: S3Client,
) -> str:
    drop_by_reason = dropped_stats.get("by_reason", {})
    report_key = f"{processed_prefix}_processing_report.json"
    s3_client.put_json(
        bucket=bucket,
        key=report_key,
        obj={
            "generated_at_epoch": time.time(),
            "dataset": dataset_name,
            "target_sampling_rate": target_rate,
            "summary": {
                "total_recordings": total_recordings,
                "ok_recordings": len(results),
                "dropped_recordings": int(dropped_stats.get("total_recordings", 0)),
                "dropped_rows": int(dropped_stats.get("total_rows", 0)),
                "dropped_recordings_missing_annotation": int(
                    dropped_stats.get("dropped_recordings_missing_annotation", 0)
                ),
                "dropped_rows_missing_annotation": int(dropped_stats.get("dropped_rows_missing_annotation", 0)),
                "error_recordings": len(failures),
            },
            "dropped": {
                "total_recordings": int(dropped_stats.get("total_recordings", 0)),
                "total_rows": int(dropped_stats.get("total_rows", 0)),
                "dropped_recordings_missing_annotation": int(
                    dropped_stats.get("dropped_recordings_missing_annotation", 0)
                ),
                "dropped_rows_missing_annotation": int(dropped_stats.get("dropped_rows_missing_annotation", 0)),
                "by_reason": dict(drop_by_reason) if isinstance(drop_by_reason, dict) else {},
            },
            "results": [asdict(item) for item in results],
            "failures": [asdict(item) for item in failures],
        },
    )
    return f"s3://{bucket}/{report_key}"


def _new_dropped_stats() -> dict[str, Any]:
    return {
        "total_rows": 0,
        "total_recordings": 0,
        "dropped_rows_missing_annotation": 0,
        "dropped_recordings_missing_annotation": 0,
        "by_reason": {},
    }


def _merge_dropped_stats(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["total_rows"] = int(target.get("total_rows", 0)) + int(source.get("total_rows", 0))
    target["total_recordings"] = int(target.get("total_recordings", 0)) + int(source.get("total_recordings", 0))
    target["dropped_rows_missing_annotation"] = int(target.get("dropped_rows_missing_annotation", 0)) + int(
        source.get("dropped_rows_missing_annotation", 0)
    )
    target["dropped_recordings_missing_annotation"] = int(
        target.get("dropped_recordings_missing_annotation", 0)
    ) + int(source.get("dropped_recordings_missing_annotation", 0))

    target_by_reason = target.get("by_reason")
    source_by_reason = source.get("by_reason")
    if not isinstance(target_by_reason, dict):
        target_by_reason = {}
        target["by_reason"] = target_by_reason
    if not isinstance(source_by_reason, dict):
        return
    for reason, count in source_by_reason.items():
        key = str(reason or "unspecified")
        target_by_reason[key] = int(target_by_reason.get(key, 0)) + int(count)


def _record_drop(
    dropped_stats: dict[str, Any],
    *,
    reason: str,
    dropped_rows: int,
    dropped_recordings: int,
) -> None:
    safe_reason = str(reason or "unspecified").strip() or "unspecified"
    safe_rows = max(0, int(dropped_rows))
    safe_recordings = max(0, int(dropped_recordings))
    if safe_rows == 0 and safe_recordings == 0:
        safe_recordings = 1

    dropped_stats["total_rows"] = int(dropped_stats.get("total_rows", 0)) + safe_rows
    dropped_stats["total_recordings"] = int(dropped_stats.get("total_recordings", 0)) + safe_recordings
    by_reason = dropped_stats.get("by_reason")
    if not isinstance(by_reason, dict):
        by_reason = {}
        dropped_stats["by_reason"] = by_reason
    by_reason[safe_reason] = int(by_reason.get(safe_reason, 0)) + safe_recordings

    if safe_reason == "missing_required_annotation":
        dropped_stats["dropped_rows_missing_annotation"] = int(
            dropped_stats.get("dropped_rows_missing_annotation", 0)
        ) + safe_rows
        dropped_stats["dropped_recordings_missing_annotation"] = int(
            dropped_stats.get("dropped_recordings_missing_annotation", 0)
        ) + safe_recordings


def _build_processing_result(
    *,
    recording,
    parquet_path: str,
    annotations_path: str | None,
    sampling_rate: int,
    original_sampling_rate: int,
) -> ProcessingResult:
    num_beat = len(recording.annotations.beat_positions or []) if recording.annotations else 0
    num_rhythm = len(recording.annotations.rhythm_episodes or []) if recording.annotations else 0
    num_labels = _count_label_entries(recording.labels)

    return ProcessingResult(
        recording_name=recording.recording_name,
        full_recording_id=recording.recording_id,
        parquet_path=parquet_path,
        annotations_path=annotations_path,
        sampling_rate=sampling_rate,
        original_sampling_rate=original_sampling_rate,
        num_channels=recording.num_channels,
        channel_names=recording.channel_names,
        duration_seconds=recording.duration_seconds,
        num_samples=recording.num_samples,
        source_format=recording.source_format,
        subject_id=recording.subject_id,
        age=recording.age,
        sex=recording.sex,
        has_beat_annotations=bool(num_beat),
        has_rhythm_annotations=bool(num_rhythm),
        has_delineation_annotations=bool(recording.annotations and recording.annotations.delineation),
        num_beat_annotations=num_beat,
        num_rhythm_episodes=num_rhythm,
        af_present=_check_af_present(recording.annotations, recording.labels),
        has_label_annotations=bool(recording.labels),
        num_label_entries=num_labels,
        slice_index=recording.slice_index,
    )


def _check_af_present(
    annotations: ECGAnnotations | None,
    labels: dict[str, Any] | None,
) -> bool | None:
    if annotations is not None and annotations.rhythm_episodes:
        for ep in annotations.rhythm_episodes:
            if ep.rhythm_type.lower() == "af":
                return True
        return False

    return _labels_indicate_af(labels)


def _labels_indicate_af(labels: dict[str, Any] | None) -> bool | None:
    if not labels:
        return None

    seen_af_signal = False
    for key, value in _walk_label_items(labels):
        key_text = str(key).strip().lower()
        if "af" in key_text or "atrial" in key_text:
            seen_af_signal = True
            parsed = _coerce_boolish(value)
            if parsed is not None:
                return parsed

        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"af", "atrial fibrillation", "atrial_fibrillation", "afib"}:
                return True

    if seen_af_signal:
        return False
    return None


def _walk_label_items(labels: dict[str, Any]):
    stack: list[tuple[str, Any]] = list(labels.items())
    while stack:
        key, value = stack.pop()
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                stack.append((f"{key}.{child_key}", child_value))
            continue
        yield key, value


def _coerce_boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value) > 0.0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "positive", "present", "af", "afib", "atrial fibrillation"}:
            return True
        if lowered in {"0", "false", "no", "negative", "absent", "none", "normal", "nsr"}:
            return False
    return None


def _count_label_entries(labels: dict[str, Any] | None) -> int:
    if not labels:
        return 0
    count = 0
    for _key, _value in _walk_label_items(labels):
        count += 1
    return count


def _is_data_key(key: str) -> bool:
    if key.endswith("/"):
        return False
    if key.endswith("_download_report.json"):
        return False
    return True
