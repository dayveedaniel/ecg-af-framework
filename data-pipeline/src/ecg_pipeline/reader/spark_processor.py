from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from ecg_pipeline.annotations import AnnotationContext, build_annotation_adapter
from ecg_pipeline.config import PipelineConfig
from ecg_pipeline.manifest import DatasetManifest
from ecg_pipeline.reader.dispatcher import FormatDispatcher
from ecg_pipeline.reader.models import ProcessingFailure, ProcessingResult, RecordingFileGroup
from ecg_pipeline.reader.processor import (
    _merge_dropped_stats,
    _new_dropped_stats,
    process_recording_group,
)
from ecg_pipeline.reader.readers import CSVReader, HDF5Reader, MATReader, WFDBReader
from ecg_pipeline.utils.s3 import S3Client
from ecg_pipeline.utils.spark import create_spark_session


@dataclass(frozen=True)
class SparkProcessJob:
    """Serializable payload broadcast to Spark executors."""

    dataset_name: str
    bucket: str
    raw_prefix: str
    processed_prefix: str
    desired_rate: int
    parquet_compression: str
    adapter_name: str
    adapter_options: dict[str, Any]
    raw_files: tuple[str, ...]
    manifest: DatasetManifest
    s3_endpoint_url: str | None
    s3_access_key: str | None
    s3_secret_key: str | None
    s3_region_name: str | None = None


def process_groups_with_spark(
    *,
    groups: list[RecordingFileGroup],
    job: SparkProcessJob,
    config: PipelineConfig,
    spark_master: str | None = None,
) -> tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]:
    """Distribute recording-group processing across Spark executors."""

    if not groups:
        return [], [], _new_dropped_stats()

    spark = create_spark_session(
        app_name=f"ecg-pipeline-{job.dataset_name}",
        master=spark_master,
        driver_memory=config.spark_driver_memory,
        executor_memory=config.spark_executor_memory,
    )
    try:
        sc = spark.sparkContext
        job_broadcast = sc.broadcast(job)
        num_slices = _partition_count(len(groups), spark_master=spark_master)
        collected = (
            sc.parallelize(groups, numSlices=num_slices)
            .mapPartitions(_PartitionMapper(job_broadcast))
            .collect()
        )
    finally:
        spark.stop()

    results: list[ProcessingResult] = []
    failures: list[ProcessingFailure] = []
    dropped_stats = _new_dropped_stats()
    for group_results, group_failures, group_dropped_stats in collected:
        results.extend(group_results)
        failures.extend(group_failures)
        _merge_dropped_stats(dropped_stats, group_dropped_stats)
    return results, failures, dropped_stats


class _PartitionMapper:
    """Picklable mapPartitions callable that resolves the broadcast job on executors."""

    def __init__(self, job_broadcast: Any) -> None:
        self.job_broadcast = job_broadcast

    def __call__(
        self, group_iter: Iterable[RecordingFileGroup]
    ) -> Iterator[tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]]:
        return _process_partition(group_iter, self.job_broadcast.value)


def _partition_count(group_count: int, *, spark_master: str | None) -> int:
    if group_count <= 0:
        return 1
    master = (spark_master or os.environ.get("SPARK_MASTER") or "local[*]").strip().lower()
    if master.startswith("local["):
        cpu_count = os.cpu_count() or 2
        return max(1, min(group_count, cpu_count))
    return max(1, min(group_count, 64))


def _process_partition(
    group_iter: Iterable[RecordingFileGroup],
    job: SparkProcessJob,
) -> Iterator[tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]]:
    groups = list(group_iter)
    if not groups:
        return iter(())

    s3_client = S3Client(
        endpoint_url=job.s3_endpoint_url,
        access_key=job.s3_access_key,
        secret_key=job.s3_secret_key,
        region_name=job.s3_region_name,
    )
    annotation_adapter = build_annotation_adapter(job.adapter_name, options=dict(job.adapter_options))
    annotation_context = AnnotationContext(
        manifest=job.manifest,
        dataset_name=job.dataset_name,
        bucket=job.bucket,
        raw_prefix=job.raw_prefix,
        raw_files=list(job.raw_files),
        s3_client=s3_client,
    )
    annotation_adapter.prepare(annotation_context)
    dispatcher = FormatDispatcher(readers=[WFDBReader(), CSVReader(), MATReader(), HDF5Reader()])

    outputs: list[tuple[list[ProcessingResult], list[ProcessingFailure], dict[str, Any]]] = []
    for group in groups:
        reader = dispatcher.get_reader_for_group(group)
        outputs.append(
            process_recording_group(
                group=group,
                dataset_name=job.dataset_name,
                bucket=job.bucket,
                raw_prefix=job.raw_prefix,
                processed_prefix=job.processed_prefix,
                desired_rate=job.desired_rate,
                parquet_compression=job.parquet_compression,
                s3_client=s3_client,
                reader=reader,
                annotation_adapter=annotation_adapter,
                annotation_context=annotation_context,
            )
        )
    return iter(outputs)
