from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ecg_pipeline.utils.s3 import S3Client

# Raw files accepted by currently supported readers.
REQUIRED_RAW_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".hea",
        ".dat",
        ".atr",
        ".qrs",
        ".csv",
        ".mat",
        ".h5",
        ".hdf5",
        ".md",
        ".txt",
    }
)


def is_required_raw_file(path_or_key: str) -> bool:
    if not path_or_key or path_or_key.endswith("/"):
        return False
    ext = Path(path_or_key).suffix.lower()
    return ext in REQUIRED_RAW_EXTENSIONS


@dataclass(frozen=True)
class PruneSummary:
    kept_files: int
    deleted_non_required_files: int
    deleted_empty_folder_markers: int


def prune_raw_prefix(
    *,
    s3_client: S3Client,
    bucket: str,
    prefix: str,
) -> PruneSummary:
    keys = list(s3_client.iter_keys(bucket, prefix))
    file_keys = [key for key in keys if not key.endswith("/")]
    folder_markers = [key for key in keys if key.endswith("/")]

    kept_file_keys = [key for key in file_keys if is_required_raw_file(key)]
    kept_file_set = set(kept_file_keys)
    deleted_non_required = 0
    for key in file_keys:
        if key in kept_file_set:
            continue
        s3_client.delete_key(bucket=bucket, key=key)
        deleted_non_required += 1

    deleted_empty_markers = 0
    # Longest-first helps nested markers be considered after child cleanup.
    for marker in sorted(folder_markers, key=len, reverse=True):
        has_children = any(k != marker and k.startswith(marker) for k in kept_file_keys)
        if has_children:
            continue
        s3_client.delete_key(bucket=bucket, key=marker)
        deleted_empty_markers += 1

    return PruneSummary(
        kept_files=len(kept_file_keys),
        deleted_non_required_files=deleted_non_required,
        deleted_empty_folder_markers=deleted_empty_markers,
    )
