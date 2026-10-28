from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from ecg_benchmark.config import BenchmarkConfig
from ecg_benchmark.splits import RecordingRecord


SPLIT_CACHE_SCHEMA_VERSION = 1
FEATURE_CACHE_SCHEMA_VERSION = 1


def cache_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def split_cache_payload(
    config: BenchmarkConfig,
    dataset_name: str,
    records: Iterable[RecordingRecord],
) -> dict[str, Any]:
    """Return the model-independent payload that identifies one dataset split."""

    record_payload = [
        {
            "full_recording_id": record.full_recording_id,
            "subject_id": record.subject_id,
            "af_present": record.af_present,
        }
        for record in sorted(records, key=lambda item: item.full_recording_id)
    ]
    return {
        "schema_version": SPLIT_CACHE_SCHEMA_VERSION,
        "kind": "splits",
        "dataset_name": dataset_name,
        "random_seed": config.random_seed,
        "split": {
            "train": config.data.split.train,
            "val": config.data.split.val,
            "test": config.data.split.test,
            "stratify_by": config.data.split.stratify_by,
        },
        "records": record_payload,
    }


def split_cache_key(
    config: BenchmarkConfig,
    dataset_name: str,
    records: Iterable[RecordingRecord],
) -> str:
    return cache_hash(split_cache_payload(config, dataset_name, records))


def feature_cache_payload(
    config: BenchmarkConfig,
    dataset_name: str,
    recordings: Iterable[Any],
) -> dict[str, Any]:
    """Return the model-independent payload that identifies generated windows."""

    recording_payload = [
        {
            "full_recording_id": recording.full_recording_id,
            "subject_id": recording.subject_id,
            "af_present": recording.af_present,
            "sampling_rate": recording.sampling_rate,
            "duration_seconds": recording.duration_seconds,
            "channel_names": list(recording.channel_names or []),
            "parquet_path": recording.parquet_path,
            "annotations_path": recording.annotations_path,
        }
        for recording in sorted(recordings, key=lambda item: item.full_recording_id)
    ]
    return {
        "schema_version": FEATURE_CACHE_SCHEMA_VERSION,
        "kind": "features",
        "dataset_name": dataset_name,
        "source_bucket": config.storage.source_bucket,
        "processed_prefix": config.data.processed_prefix,
        "sample_rate_hz": config.data.sample_rate_hz,
        "window_seconds": config.data.window_seconds,
        "stride_seconds": config.data.stride_seconds,
        "label_policy": config.data.label_policy,
        "lead_policy": config.data.lead_policy,
        "preferred_leads": list(config.data.preferred_leads),
        "recordings": recording_payload,
    }


def feature_cache_key(
    config: BenchmarkConfig,
    dataset_name: str,
    recordings: Iterable[Any],
) -> str:
    return cache_hash(feature_cache_payload(config, dataset_name, recordings))
