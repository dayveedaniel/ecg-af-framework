from __future__ import annotations

import importlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ecg_pipeline.reader.models import ECGRecording, RecordingFileGroup
from ecg_pipeline.reader.readers._common import (
    DEFAULT_SAMPLE_RATE_HZ,
    default_channel_names,
    flatten_strings,
    infer_sample_rate_from_time_values,
    normalize_sample_rate,
    normalize_signal_matrix,
    parse_sample_rate_from_text,
)
from ecg_pipeline.reader.readers.base import BaseReader

_RATE_KEY_RE = re.compile(r"(fs|sampling[_\s-]*rate|sample[_\s-]*rate|frequency|freq)", re.IGNORECASE)
_TIME_KEY_RE = re.compile(r"(time|timestamp|t$)", re.IGNORECASE)
_SIGNAL_POSITIVE_HINT_RE = re.compile(r"(signal|ecg|wave|trace|lead|val|data)", re.IGNORECASE)
_SIGNAL_NEGATIVE_HINT_RE = re.compile(r"(label|annot|rhythm|beat|rpeak|qrs|index|time|fs|freq)", re.IGNORECASE)
_CHANNEL_KEY_RE = re.compile(r"(lead|channel|sig_name|channel_names|labels)", re.IGNORECASE)
_UNITS_KEY_RE = re.compile(r"(unit|units)", re.IGNORECASE)


@dataclass
class _SignalCandidate:
    path: str
    array: np.ndarray
    score: float


class MATReader(BaseReader):
    format_name = "mat"
    _exts = {".mat"}

    def can_read(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in self._exts

    def discover_recordings(self, file_list: list[str]) -> list[RecordingFileGroup]:
        groups: list[RecordingFileGroup] = []
        for rel in sorted(file_list):
            if not self.can_read(rel):
                continue
            path = Path(rel)
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
        mat_path = local_dataset_dir / file_group.primary_file
        loadmat = _loadmat_func()
        payload = loadmat(mat_path, squeeze_me=True, struct_as_record=False)

        signal_raw = _select_signal_array(payload)
        signal = normalize_signal_matrix(signal_raw)

        sampling_rate = _find_sampling_rate(payload, num_samples=signal.shape[0]) or DEFAULT_SAMPLE_RATE_HZ
        channel_names = _find_channel_names(payload, expected_channels=signal.shape[1])
        units = _find_units(payload)

        recording_name = file_group.recording_base_name
        return [
            ECGRecording(
                recording_id=f"{dataset_name}/{recording_name}",
                recording_name=recording_name,
                original_filename=file_group.primary_file,
                signal=signal,
                sampling_rate=sampling_rate,
                num_channels=int(signal.shape[1]),
                channel_names=channel_names,
                duration_seconds=float(signal.shape[0]) / float(sampling_rate),
                num_samples=int(signal.shape[0]),
                source_format=self.format_name,
                units=units,
                subject_id=None,
                age=None,
                sex=None,
                annotations=None,
            )
        ]


def _iter_named_values(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("__"):
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_named_values(item, path)
        return

    if isinstance(value, np.ndarray):
        if value.dtype == object:
            for idx, item in enumerate(value.flat):
                path = f"{prefix}[{idx}]"
                yield from _iter_named_values(item, path)
            return
        yield prefix, value
        return

    field_names = getattr(value, "_fieldnames", None)
    if field_names:
        for key in field_names:
            item = getattr(value, key, None)
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_named_values(item, path)
        return

    if isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            path = f"{prefix}[{idx}]"
            yield from _iter_named_values(item, path)
        return

    yield prefix, value


def _select_signal_array(payload: dict[str, Any]) -> np.ndarray:
    candidates: list[_SignalCandidate] = []
    for path, value in _iter_named_values(payload):
        if not isinstance(value, np.ndarray):
            continue
        if value.dtype.kind not in {"i", "u", "f"}:
            continue
        if value.size < 40:
            continue

        arr = np.asarray(value, dtype=np.float64)
        if arr.ndim > 2:
            arr = np.squeeze(arr)
        if arr.ndim not in {1, 2}:
            continue

        score = float(np.log1p(arr.size))
        if _SIGNAL_POSITIVE_HINT_RE.search(path):
            score += 12.0
        if _SIGNAL_NEGATIVE_HINT_RE.search(path):
            score -= 8.0
        if arr.ndim == 2:
            score += 4.0
        candidates.append(_SignalCandidate(path=path, array=arr, score=score))

    if not candidates:
        raise ValueError("MAT file has no numeric signal-like array")
    return max(candidates, key=lambda item: item.score).array


def _find_sampling_rate(payload: dict[str, Any], *, num_samples: int) -> int | None:
    best_rate = None
    for path, value in _iter_named_values(payload):
        if not _RATE_KEY_RE.search(path):
            continue
        scalar = _coerce_scalar_number(value)
        normalized = normalize_sample_rate(scalar)
        if normalized is not None:
            best_rate = normalized
            break

    if best_rate is not None:
        return best_rate

    for path, value in _iter_named_values(payload):
        if not _TIME_KEY_RE.search(path):
            continue
        if not isinstance(value, np.ndarray):
            continue
        if value.dtype.kind not in {"i", "u", "f"}:
            continue
        arr = np.asarray(value, dtype=np.float64).reshape(-1)
        if arr.size < min(8, num_samples):
            continue
        inferred = infer_sample_rate_from_time_values(arr)
        if inferred is not None:
            return inferred

    header = payload.get("__header__")
    parsed_header_rate = parse_sample_rate_from_text(str(header))
    if parsed_header_rate is not None:
        return parsed_header_rate
    return None


def _coerce_scalar_number(value: Any) -> float | None:
    if isinstance(value, np.ndarray):
        if value.size != 1:
            return None
        return _coerce_scalar_number(value.reshape(-1)[0])
    if isinstance(value, (int, float, np.integer, np.floating)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return None


def _find_channel_names(payload: dict[str, Any], *, expected_channels: int) -> list[str]:
    for path, value in _iter_named_values(payload):
        if not _CHANNEL_KEY_RE.search(path):
            continue
        labels = flatten_strings(value)
        if len(labels) == expected_channels:
            return default_channel_names(expected_channels, labels)
    return default_channel_names(expected_channels)


def _find_units(payload: dict[str, Any]) -> str:
    for path, value in _iter_named_values(payload):
        if not _UNITS_KEY_RE.search(path):
            continue
        labels = flatten_strings(value)
        if not labels:
            continue
        lowered = labels[0].lower()
        if "mv" in lowered:
            return "mV"
        return "unknown"
    return "unknown"


def _loadmat_func():
    try:
        return importlib.import_module("scipy.io").loadmat
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MAT support requires dependency 'scipy'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
