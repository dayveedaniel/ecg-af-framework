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
    data: np.ndarray
    score: float


class HDF5Reader(BaseReader):
    format_name = "hdf5"
    _exts = {".h5", ".hdf5"}

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
        options = dict(reader_options or {})
        h5py = _load_h5py()
        h5_path = local_dataset_dir / file_group.primary_file

        with h5py.File(h5_path, "r") as handle:
            signal_raw = _select_signal_array(handle)
            units = _find_units(handle)

            if signal_raw.ndim in {1, 2}:
                signal = normalize_signal_matrix(signal_raw)
                sampling_rate = _find_sampling_rate(handle, num_samples=signal.shape[0]) or DEFAULT_SAMPLE_RATE_HZ
                channel_names = _find_channel_names(handle, expected_channels=signal.shape[1])
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
                        slice_index=None,
                    )
                ]

            if signal_raw.ndim != 3:
                raise ValueError(f"Unsupported HDF5 signal dimensionality for {file_group.primary_file}: {signal_raw.shape}")

            batch_axis = int(options.get("batch_axis", 0))
            sample_axis = int(options.get("sample_axis", 1))
            channel_axis = int(options.get("channel_axis", 2))
            batch = _normalize_batch_tensor(
                signal_raw,
                batch_axis=batch_axis,
                sample_axis=sample_axis,
                channel_axis=channel_axis,
            )

            num_records = int(batch.shape[0])
            num_samples = int(batch.shape[1])
            num_channels = int(batch.shape[2])
            if num_records < 1:
                raise ValueError(f"HDF5 signal batch has no recordings: {file_group.primary_file}")

            sampling_rate = _find_sampling_rate(handle, num_samples=num_samples) or DEFAULT_SAMPLE_RATE_HZ
            channel_names = _find_channel_names(handle, expected_channels=num_channels)

            width = int(options.get("recording_name_width", max(6, len(str(max(num_records - 1, 0))))))
            recordings: list[ECGRecording] = []
            for idx in range(num_records):
                signal = normalize_signal_matrix(batch[idx, :, :])
                recording_name = f"{file_group.recording_base_name}_{idx:0{width}d}"
                recordings.append(
                    ECGRecording(
                        recording_id=f"{dataset_name}/{recording_name}",
                        recording_name=recording_name,
                        original_filename=file_group.primary_file,
                        signal=signal,
                        sampling_rate=sampling_rate,
                        num_channels=num_channels,
                        channel_names=channel_names,
                        duration_seconds=float(signal.shape[0]) / float(sampling_rate),
                        num_samples=int(signal.shape[0]),
                        source_format=self.format_name,
                        units=units,
                        subject_id=None,
                        age=None,
                        sex=None,
                        annotations=None,
                        slice_index=idx,
                    )
                )
            return recordings


def _iter_datasets(handle):
    h5py = _load_h5py()
    items: list[tuple[str, Any]] = []

    def visitor(name: str, obj: Any) -> None:
        if isinstance(obj, h5py.Dataset):
            items.append((name, obj))

    handle.visititems(visitor)
    return items


def _select_signal_array(handle) -> np.ndarray:
    candidates: list[_SignalCandidate] = []
    for path, dset in _iter_datasets(handle):
        if dset.dtype.kind not in {"i", "u", "f"}:
            continue
        if dset.size < 40:
            continue

        data = np.asarray(dset[()], dtype=np.float64)
        data = np.squeeze(data)
        if data.ndim not in {1, 2, 3}:
            continue

        score = float(np.log1p(data.size))
        if _SIGNAL_POSITIVE_HINT_RE.search(path):
            score += 12.0
        if _SIGNAL_NEGATIVE_HINT_RE.search(path):
            score -= 8.0
        if data.ndim == 2:
            score += 4.0
        if data.ndim == 3:
            score += 6.0
        candidates.append(_SignalCandidate(path=path, data=data, score=score))

    if not candidates:
        raise ValueError("HDF5 file has no numeric signal-like dataset")
    return max(candidates, key=lambda item: item.score).data


def _find_sampling_rate(handle, *, num_samples: int) -> int | None:
    for key, value in handle.attrs.items():
        if not _RATE_KEY_RE.search(str(key)):
            continue
        normalized = normalize_sample_rate(_coerce_scalar_number(value))
        if normalized is not None:
            return normalized

    for path, dset in _iter_datasets(handle):
        if _RATE_KEY_RE.search(path):
            normalized = normalize_sample_rate(_coerce_scalar_number(dset[()]))
            if normalized is not None:
                return normalized

    for path, dset in _iter_datasets(handle):
        if not _TIME_KEY_RE.search(path):
            continue
        if dset.dtype.kind not in {"i", "u", "f"}:
            continue
        arr = np.asarray(dset[()], dtype=np.float64).reshape(-1)
        if arr.size < min(8, num_samples):
            continue
        inferred = infer_sample_rate_from_time_values(arr)
        if inferred is not None:
            return inferred

    for key, value in handle.attrs.items():
        if not isinstance(value, (bytes, str)):
            continue
        parsed = parse_sample_rate_from_text(str(value))
        if parsed is not None:
            return parsed
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


def _find_channel_names(handle, *, expected_channels: int) -> list[str]:
    for key, value in handle.attrs.items():
        if not _CHANNEL_KEY_RE.search(str(key)):
            continue
        labels = flatten_strings(value)
        if len(labels) == expected_channels:
            return default_channel_names(expected_channels, labels)

    for path, dset in _iter_datasets(handle):
        if not _CHANNEL_KEY_RE.search(path):
            continue
        labels = flatten_strings(dset[()])
        if len(labels) == expected_channels:
            return default_channel_names(expected_channels, labels)

    return default_channel_names(expected_channels)


def _find_units(handle) -> str:
    for key, value in handle.attrs.items():
        if not _UNITS_KEY_RE.search(str(key)):
            continue
        text_items = flatten_strings(value)
        if not text_items:
            continue
        lowered = text_items[0].lower()
        if "mv" in lowered:
            return "mV"
        return "unknown"
    return "unknown"


def _normalize_batch_tensor(
    signal: np.ndarray,
    *,
    batch_axis: int,
    sample_axis: int,
    channel_axis: int,
) -> np.ndarray:
    if signal.ndim != 3:
        raise ValueError(f"Expected a 3D signal tensor, got shape={signal.shape}")

    b = _normalize_axis(batch_axis, ndim=3)
    s = _normalize_axis(sample_axis, ndim=3)
    c = _normalize_axis(channel_axis, ndim=3)
    if len({b, s, c}) != 3:
        raise ValueError(
            f"batch_axis/sample_axis/channel_axis must be distinct. Got: batch={batch_axis}, sample={sample_axis}, channel={channel_axis}"
        )

    moved = np.moveaxis(signal, [b, s, c], [0, 1, 2])
    return np.asarray(moved, dtype=np.float64)


def _normalize_axis(value: int, *, ndim: int) -> int:
    axis = int(value)
    if axis < 0:
        axis += ndim
    if axis < 0 or axis >= ndim:
        raise ValueError(f"Axis {value} is out of range for ndim={ndim}")
    return axis


def _load_h5py():
    try:
        return importlib.import_module("h5py")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "HDF5 support requires dependency 'h5py'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
