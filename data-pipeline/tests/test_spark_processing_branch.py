from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ecg_pipeline.config import PipelineConfig
from ecg_pipeline.manifest import AnnotationsInfo, DatasetInfo, DatasetManifest, SourceInfo
from ecg_pipeline.reader import processor
from ecg_pipeline.reader.models import ProcessingFailure, ProcessingResult, RecordingFileGroup


def _pipeline_config(*, min_recordings_for_spark: int = 50) -> PipelineConfig:
    return PipelineConfig(
        s3_bucket="ecg-pipeline",
        default_target_sample_rate=250,
        default_normalization="none",
        parquet_compression="snappy",
        min_recordings_for_spark=min_recordings_for_spark,
        spark_driver_memory="1g",
        spark_executor_memory="1g",
        airflow_max_active_datasets=1,
        airflow_task_retries=0,
        airflow_retry_delay_seconds=1,
        airflow_retry_exponential_backoff=False,
        download_timeout_seconds=30,
        download_chunk_size_bytes=1024,
        download_sleep_between_datasets_seconds=0.0,
        physionet_s3_bucket="physionet-open",
    )


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        dataset=DatasetInfo(name="demo", display_name="Demo"),
        source=SourceInfo(url="https://example.com/demo.zip", provider="direct_url"),
        annotations=AnnotationsInfo(adapter="none", options={}),
    )


def _group(index: int) -> RecordingFileGroup:
    name = f"rec{index:03d}"
    return RecordingFileGroup(
        recording_base_name=name,
        primary_file=f"{name}.csv",
        associated_files=[f"{name}.csv"],
        detected_format="csv",
    )


def _ok_result(name: str) -> ProcessingResult:
    return ProcessingResult(
        recording_name=name,
        full_recording_id=f"demo/{name}",
        parquet_path=f"s3://ecg-pipeline/processed/250hz/demo/{name}.parquet",
        annotations_path=None,
        sampling_rate=250,
        original_sampling_rate=250,
        num_channels=1,
        channel_names=["I"],
        duration_seconds=1.0,
        num_samples=250,
        source_format="csv",
        subject_id=None,
        age=None,
        sex=None,
        has_beat_annotations=False,
        has_rhythm_annotations=False,
        has_delineation_annotations=False,
        num_beat_annotations=0,
        num_rhythm_episodes=0,
        af_present=None,
        has_label_annotations=False,
        num_label_entries=0,
        slice_index=None,
    )


@pytest.fixture
def process_harness(monkeypatch: pytest.MonkeyPatch):
    calls: dict[str, Any] = {"spark": 0, "sequential": 0}

    def fake_iter_keys(bucket: str, prefix: str):
        return [f"{prefix}rec000.csv"]

    monkeypatch.setattr(
        processor,
        "build_annotation_adapter",
        lambda adapter, options=None: MagicMock(
            prepare=MagicMock(),
            sidecar_files=MagicMock(return_value=set()),
        ),
    )

    class FakeDispatcher:
        def __init__(self, readers=None):
            self._groups: list[RecordingFileGroup] = []

        def discover_and_group(self, signal_files: list[str]) -> list[RecordingFileGroup]:
            return list(self._groups)

        def get_reader_for_group(self, group: RecordingFileGroup):
            return MagicMock()

    dispatcher = FakeDispatcher()

    def fake_dispatcher_ctor(*, readers=None):
        return dispatcher

    monkeypatch.setattr(processor, "FormatDispatcher", fake_dispatcher_ctor)

    def fake_sequential(**kwargs):
        calls["sequential"] += 1
        groups = kwargs["groups"]
        return [_ok_result(g.recording_base_name) for g in groups], [], processor._new_dropped_stats()

    def fake_spark(**kwargs):
        calls["spark"] += 1
        groups = kwargs["groups"]
        return [_ok_result(g.recording_base_name) for g in groups], [], processor._new_dropped_stats()

    monkeypatch.setattr(processor, "_process_groups_sequential", fake_sequential)
    monkeypatch.setattr(processor, "_process_groups_via_spark", fake_spark)
    monkeypatch.setattr(
        processor,
        "write_processing_report",
        lambda **kwargs: "s3://ecg-pipeline/processed/250hz/demo/_processing_report.json",
    )

    s3 = MagicMock()
    s3.iter_keys.side_effect = fake_iter_keys
    s3.endpoint_url = "http://localhost:9000"
    s3.access_key = "minio"
    s3.secret_key = "minio123"
    s3.region_name = "us-east-1"

    def run(*, group_count: int, min_recordings_for_spark: int):
        dispatcher._groups = [_group(i) for i in range(group_count)]
        return processor.process_dataset(
            manifest=_manifest(),
            s3_client=s3,
            config=_pipeline_config(min_recordings_for_spark=min_recordings_for_spark),
        ), calls

    return run


def test_uses_sequential_below_spark_threshold(process_harness) -> None:
    _, calls = process_harness(group_count=3, min_recordings_for_spark=50)
    assert calls["sequential"] == 1
    assert calls["spark"] == 0


def test_uses_spark_at_or_above_threshold(process_harness) -> None:
    _, calls = process_harness(group_count=50, min_recordings_for_spark=50)
    assert calls["spark"] == 1
    assert calls["sequential"] == 0


def test_spark_fallback_uses_sequential(monkeypatch: pytest.MonkeyPatch) -> None:
    from ecg_pipeline.reader.spark_processor import SparkProcessJob

    job = SparkProcessJob(
        dataset_name="demo",
        bucket="ecg-pipeline",
        raw_prefix="raw/demo/",
        processed_prefix="processed/250hz/demo/",
        desired_rate=250,
        parquet_compression="snappy",
        adapter_name="none",
        adapter_options={},
        raw_files=("rec000.csv",),
        manifest=_manifest(),
        s3_endpoint_url="http://localhost:9000",
        s3_access_key="minio",
        s3_secret_key="minio123",
    )
    groups = [_group(0)]

    def boom(**kwargs):
        raise RuntimeError("no java")

    monkeypatch.setattr(
        "ecg_pipeline.reader.spark_processor.process_groups_with_spark",
        boom,
    )

    sequential_calls = {"n": 0}

    def fake_sequential(**kwargs):
        sequential_calls["n"] += 1
        return [_ok_result("rec000")], [ProcessingFailure("x", "y", "z")], processor._new_dropped_stats()

    monkeypatch.setattr(processor, "_process_groups_sequential", fake_sequential)
    monkeypatch.setattr(
        processor,
        "build_annotation_adapter",
        lambda adapter, options=None: MagicMock(prepare=MagicMock()),
    )
    monkeypatch.setattr(processor, "FormatDispatcher", lambda readers=None: MagicMock())

    s3 = MagicMock()
    s3.endpoint_url = job.s3_endpoint_url
    s3.access_key = job.s3_access_key
    s3.secret_key = job.s3_secret_key
    s3.region_name = "us-east-1"

    with pytest.warns(RuntimeWarning, match="Spark processing unavailable"):
        results, failures, dropped = processor._process_groups_via_spark(
            groups=groups,
            manifest=_manifest(),
            s3_client=s3,
            config=_pipeline_config(min_recordings_for_spark=1),
            dataset_name="demo",
            bucket="ecg-pipeline",
            raw_prefix="raw/demo/",
            processed_prefix="processed/250hz/demo/",
            desired_rate=250,
            raw_files=["rec000.csv"],
            spark_master="local[*]",
        )

    assert sequential_calls["n"] == 1
    assert len(results) == 1
    assert len(failures) == 1
    assert dropped["total_recordings"] == 0
