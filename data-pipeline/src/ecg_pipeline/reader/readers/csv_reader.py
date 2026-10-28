from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ecg_pipeline.reader.models import ECGRecording, RecordingFileGroup
from ecg_pipeline.reader.readers._common import (
    DEFAULT_SAMPLE_RATE_HZ,
    default_channel_names,
    infer_sample_rate_from_time_values,
    normalize_sample_rate,
    normalize_signal_matrix,
    parse_sample_rate_from_text,
)
from ecg_pipeline.reader.readers.base import BaseReader

_IGNORE_CSV_BASENAMES = {
    "readme",
    "license",
    "licenses",
    "checksum",
    "checksums",
    "sha256sums",
    "md5sums",
    "records",
    "annotators",
}
_TIME_COL_RE = re.compile(r"(?:^|_)(time|timestamp|t)(?:$|_)", flags=re.IGNORECASE)


class CSVReader(BaseReader):
    format_name = "csv"
    _exts = {".csv"}

    def can_read(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in self._exts

    def discover_recordings(self, file_list: list[str]) -> list[RecordingFileGroup]:
        groups: list[RecordingFileGroup] = []
        for rel in sorted(file_list):
            path = Path(rel)
            if not self.can_read(rel):
                continue
            if path.stem.lower() in _IGNORE_CSV_BASENAMES:
                continue
            groups.append(
                RecordingFileGroup(
                    recording_base_name=path.stem,
                    primary_file=rel,
                    associated_files=[rel],
                    detected_format=self.format_name,
                )
            )
        return groups

    def read_local_many(
        self,
        *,
        file_group: RecordingFileGroup,
        local_dataset_dir: Path,
        dataset_name: str,
        reader_options: dict[str, Any] | None = None,
    ) -> list[ECGRecording]:
        csv_path = local_dataset_dir / file_group.primary_file
        header_text = _read_header_text(csv_path)

        try:
            df = pd.read_csv(csv_path, sep=None, engine="python", comment="#")
        except Exception:
            df = pd.read_csv(csv_path, comment="#")

        if df.empty:
            raise ValueError(f"CSV file has no rows: {file_group.primary_file}")

        signal, channel_names, rate_from_time = _extract_signal(df)

        rate = (
            rate_from_time
            or parse_sample_rate_from_text(header_text)
            or _parse_rate_from_sidecars(csv_path)
            or DEFAULT_SAMPLE_RATE_HZ
        )
        sampling_rate = normalize_sample_rate(rate) or DEFAULT_SAMPLE_RATE_HZ

        return [
            ECGRecording(
                recording_id=f"{dataset_name}/{file_group.recording_base_name}",
                recording_name=file_group.recording_base_name,
                original_filename=file_group.primary_file,
                signal=signal,
                sampling_rate=sampling_rate,
                num_channels=int(signal.shape[1]),
                channel_names=channel_names,
                duration_seconds=float(signal.shape[0]) / float(sampling_rate),
                num_samples=int(signal.shape[0]),
                source_format=self.format_name,
                units="unknown",
                subject_id=None,
                age=None,
                sex=None,
                annotations=None,
            )
        ]


def _read_header_text(path: Path, *, max_lines: int = 60) -> str:
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for _ in range(max_lines):
            line = handle.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
    return "\n".join(lines)


def _parse_rate_from_sidecars(csv_path: Path) -> int | None:
    for suffix in (".txt", ".meta", ".json", ".yaml", ".yml"):
        candidate = csv_path.with_suffix(suffix)
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        parsed = parse_sample_rate_from_text(text)
        if parsed:
            return parsed
    return None


def _extract_signal(df: pd.DataFrame) -> tuple[np.ndarray, list[str], int | None]:
    numeric_cols: dict[str, np.ndarray] = {}
    for col in df.columns:
        coerced = pd.to_numeric(df[col], errors="coerce")
        valid = coerced.notna().sum()
        if valid == 0:
            continue
        if valid / max(len(df), 1) < 0.8:
            continue
        numeric_cols[str(col)] = coerced.to_numpy(dtype=np.float64)

    if not numeric_cols:
        raise ValueError("CSV file has no numeric columns suitable for ECG signal")

    time_col = _pick_time_column(numeric_cols)
    signal_cols = list(numeric_cols.keys())
    if time_col and len(signal_cols) > 1:
        signal_cols = [c for c in signal_cols if c != time_col]

    matrix = np.column_stack([numeric_cols[c] for c in signal_cols])
    mask = np.all(np.isfinite(matrix), axis=1)
    if mask.sum() == 0:
        raise ValueError("CSV numeric columns do not contain valid rows")
    matrix = matrix[mask]

    # Handle horizontal single-row exports where each CSV column is a time sample.
    if matrix.shape[0] == 1 and matrix.shape[1] > 8:
        signal = matrix[0, :].reshape(-1, 1)
        channel_names = ["channel_0"]
    else:
        if matrix.shape[0] < 2:
            raise ValueError("CSV numeric columns do not contain at least two valid rows")
        signal = normalize_signal_matrix(matrix)
        channel_names = default_channel_names(signal.shape[1], signal_cols)

    inferred_rate = None
    if time_col:
        time_values = numeric_cols[time_col][mask]
        inferred_rate = infer_sample_rate_from_time_values(time_values)

    return signal, channel_names, inferred_rate


def _pick_time_column(columns: dict[str, np.ndarray]) -> str | None:
    for name, values in columns.items():
        if not _TIME_COL_RE.search(name):
            continue
        arr = np.asarray(values, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size < 3:
            continue
        diffs = np.diff(arr)
        if np.all(diffs > 0):
            return name
    return None
