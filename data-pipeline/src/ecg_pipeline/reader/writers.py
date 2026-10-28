from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from ecg_pipeline.reader.models import ECGRecording
from ecg_pipeline.utils.s3 import S3Client


def write_parquet(
    *,
    recording: ECGRecording,
    dataset_name: str,
    bucket: str,
    processed_prefix: str,
    target_rate: int,
    original_rate: int,
    parquet_compression: str,
    s3_client: S3Client,
) -> str:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    df = _recording_to_dataframe(recording)
    table = pa.Table.from_pandas(df, preserve_index=False)
    metadata = {
        "recording_id": recording.recording_id,
        "dataset_name": dataset_name,
        "sampling_rate": str(target_rate),
        "original_sampling_rate": str(original_rate),
        "num_channels": str(recording.num_channels),
        "channel_names": json.dumps(recording.channel_names),
        "duration_seconds": str(recording.duration_seconds),
        "units": recording.units,
        "source_format": recording.source_format,
        "original_filename": recording.original_filename,
        "slice_index": "" if recording.slice_index is None else str(recording.slice_index),
    }
    existing_meta = table.schema.metadata or {}
    full_meta = {**existing_meta, **{k.encode("utf-8"): v.encode("utf-8") for k, v in metadata.items()}}
    table = table.replace_schema_metadata(full_meta)

    key = f"{processed_prefix}{_safe_recording_filename(recording.recording_name)}.parquet"

    with tempfile.NamedTemporaryFile(prefix="ecg-parquet-", suffix=".parquet", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        pq.write_table(table, tmp_path, compression=parquet_compression)
        s3_client.upload_file(bucket=bucket, key=key, filename=tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return f"s3://{bucket}/{key}"


def write_annotations_json(
    *,
    recording: ECGRecording,
    bucket: str,
    processed_prefix: str,
    target_rate: int,
    original_rate: int,
    s3_client: S3Client,
) -> str:
    if recording.annotations is None and not recording.labels:
        raise ValueError("Cannot write annotations JSON for recording without annotations or labels")

    beat_annotations = None
    if recording.annotations is not None and recording.annotations.beat_positions is not None:
        beat_annotations = {
            "positions": recording.annotations.beat_positions,
            "labels": recording.annotations.beat_labels or [],
        }

    rhythm_episodes = None
    if recording.annotations is not None and recording.annotations.rhythm_episodes is not None:
        rhythm_episodes = [asdict(ep) for ep in recording.annotations.rhythm_episodes]

    delineation = None
    if recording.annotations is not None and recording.annotations.delineation is not None:
        delineation = asdict(recording.annotations.delineation)

    doc = {
        "recording_id": recording.recording_id,
        "slice_index": recording.slice_index,
        "annotation_source": "original",
        "target_sampling_rate": target_rate,
        "original_sampling_rate": original_rate,
        "beat_annotations": beat_annotations,
        "rhythm_episodes": rhythm_episodes,
        "delineation": delineation,
        "labels": recording.labels,
    }

    key = f"{processed_prefix}{_safe_recording_filename(recording.recording_name)}.annotations.json"
    s3_client.put_json(bucket=bucket, key=key, obj=doc)
    return f"s3://{bucket}/{key}"


def _recording_to_dataframe(recording: ECGRecording):
    import pandas as pd

    num_channels = int(recording.signal.shape[1])
    columns = {"sample_index": list(range(int(recording.signal.shape[0])))}
    for idx in range(num_channels):
        name = _channel_column_name(idx, recording.channel_names)
        columns[name] = recording.signal[:, idx]
    return pd.DataFrame(columns)


def _channel_column_name(index: int, channel_names: list[str]) -> str:
    if 0 <= index < len(channel_names) and channel_names[index]:
        return f"channel_{index}_{channel_names[index]}"
    return f"channel_{index}"


def _safe_recording_filename(name: str) -> str:
    return name.replace("\\", "__").replace("/", "__")
