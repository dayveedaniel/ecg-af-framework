from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import numpy as np

from ecg_pipeline.reader.models import ECGRecording, RecordingFileGroup
from ecg_pipeline.reader.readers.base import BaseReader


class WFDBReader(BaseReader):
    format_name = "wfdb"
    _wfdb_exts = {".hea", ".dat", ".atr", ".qrs"}

    def can_read(self, filepath: str) -> bool:
        return Path(filepath).suffix.lower() in self._wfdb_exts

    def discover_recordings(self, file_list: list[str]) -> list[RecordingFileGroup]:
        files = [f for f in file_list if self.can_read(f)]
        files_set = set(files)
        groups: list[RecordingFileGroup] = []
        for hea in sorted(f for f in files if f.lower().endswith(".hea")):
            base = hea[: -len(".hea")]
            associated = [candidate for candidate in files_set if candidate.startswith(f"{base}.")]
            if hea not in associated:
                associated.append(hea)
            groups.append(
                RecordingFileGroup(
                    recording_base_name=Path(base).name,
                    primary_file=hea,
                    associated_files=sorted(associated),
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
        primary = local_dataset_dir / file_group.primary_file
        record_name = str(primary.with_suffix(""))
        wfdb = _load_wfdb()
        record = wfdb.rdrecord(record_name, physical=True)

        signal = _normalize_signal(record.p_signal)
        num_samples, num_channels = signal.shape
        sampling_rate = int(round(float(record.fs)))
        channel_names = [str(x) for x in (record.sig_name or [])]
        if not channel_names:
            channel_names = [f"channel_{i}" for i in range(num_channels)]

        recording_name = file_group.recording_base_name
        return [
            ECGRecording(
                recording_id=f"{dataset_name}/{recording_name}",
                recording_name=recording_name,
                original_filename=file_group.primary_file,
                signal=signal,
                sampling_rate=sampling_rate,
                num_channels=num_channels,
                channel_names=channel_names,
                duration_seconds=(num_samples / sampling_rate) if sampling_rate > 0 else 0.0,
                num_samples=num_samples,
                source_format=self.format_name,
                units="mV",
                age=None,
                sex=None,
                annotations=None,
            )
        ]


def _normalize_signal(signal: np.ndarray) -> np.ndarray:
    if signal is None:
        raise ValueError("WFDB record has no signal data")
    arr = np.asarray(signal, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D signal array, got shape={arr.shape}")
    return arr


def _load_wfdb():
    try:
        return importlib.import_module("wfdb")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "WFDB support requires dependency 'wfdb'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
