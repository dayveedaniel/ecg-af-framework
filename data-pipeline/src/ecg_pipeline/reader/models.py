from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RhythmEpisode:
    start_sample: int
    end_sample: int
    rhythm_type: str
    original_label: str | None = None


@dataclass(frozen=True)
class Delineation:
    p_onsets: list[int] | None = None
    p_offsets: list[int] | None = None
    qrs_onsets: list[int] | None = None
    qrs_offsets: list[int] | None = None
    t_onsets: list[int] | None = None
    t_offsets: list[int] | None = None


@dataclass(frozen=True)
class ECGAnnotations:
    beat_positions: list[int] | None = None
    beat_labels: list[str] | None = None
    rhythm_episodes: list[RhythmEpisode] | None = None
    delineation: Delineation | None = None


@dataclass
class ECGRecording:
    recording_id: str
    recording_name: str
    original_filename: str
    signal: np.ndarray
    sampling_rate: int
    num_channels: int
    channel_names: list[str]
    duration_seconds: float
    num_samples: int
    source_format: str
    units: str = "mV"
    subject_id: str | None = None
    age: int | None = None
    sex: str | None = None
    annotations: ECGAnnotations | None = None
    slice_index: int | None = None
    labels: dict[str, object] | None = None


@dataclass(frozen=True)
class RecordingFileGroup:
    recording_base_name: str
    primary_file: str
    associated_files: list[str]
    detected_format: str


@dataclass(frozen=True)
class ProcessingResult:
    recording_name: str
    full_recording_id: str
    parquet_path: str
    annotations_path: str | None
    sampling_rate: int
    original_sampling_rate: int
    num_channels: int
    channel_names: list[str]
    duration_seconds: float
    num_samples: int
    source_format: str
    subject_id: str | None
    age: int | None
    sex: str | None
    has_beat_annotations: bool
    has_rhythm_annotations: bool
    has_delineation_annotations: bool
    num_beat_annotations: int
    num_rhythm_episodes: int
    af_present: bool | None
    has_label_annotations: bool
    num_label_entries: int
    slice_index: int | None


@dataclass(frozen=True)
class ProcessingFailure:
    recording_name: str
    primary_file: str
    error: str
