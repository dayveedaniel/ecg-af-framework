from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecg_pipeline.annotations.base import AnnotationAdapter, AnnotationDrop
from ecg_pipeline.annotations.context import AnnotationContext
from ecg_pipeline.annotations.registry import register_annotation_adapter
from ecg_pipeline.reader.models import ECGAnnotations, ECGRecording, RecordingFileGroup, RhythmEpisode

# ECG signal files look like `record_000_ecg_00.h5`. RR-interval files look like
# `record_000_rr_00.h5`. Episode sidecars look like `record_000_ecg_labels.csv`.
_ECG_FILE_RE = re.compile(r"(?P<record_id>record_\d+)_ecg_(?P<file_index>\d+)$", re.IGNORECASE)
_RR_FILE_RE = re.compile(r"record_\d+_rr_\d+$", re.IGNORECASE)
_ECG_LABELS_RE = re.compile(r"(?P<record_id>record_\d+)_ecg_labels$", re.IGNORECASE)


@dataclass(frozen=True)
class _AFInterval:
    start_file_index: int
    start_sample: int
    end_file_index: int
    end_sample: int


@dataclass(frozen=True)
class _SubjectMeta:
    patient_id: str | None
    age: int | None
    sex: str | None


@register_annotation_adapter("iridia_af_v1")
class IridiaAFAdapter(AnnotationAdapter):
    _CANONICAL_MAPPING_VERSION = "iridia_af_rhythm_v1"
    _SOURCE_LABEL_CONVENTION = "iridia_af_ecg_episode_csv_sample_index"

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._sampling_rate_hz = _opt_positive_int(self.options.get("sampling_rate_hz"))
        self._calibration_samples = max(0, int(self.options.get("calibration_samples", 0) or 0))
        self._sidecars: set[str] = set()
        self._episodes_by_record: dict[str, list[_AFInterval]] = {}
        self._labels_file_by_record: dict[str, str] = {}
        self._meta_by_record: dict[str, _SubjectMeta] = {}

    def prepare(self, context: AnnotationContext) -> None:
        ecg_files = [p for p in context.raw_files if _is_ecg_signal_file(p)]
        if not ecg_files:
            raise FileNotFoundError(
                "IRIDIA-AF adapter found no '*_ecg_*.h5' waveform files in raw dataset files"
            )

        # Everything that is not an ECG waveform file is excluded from signal discovery:
        # RR-interval HDF5 files and all CSV sidecars (metadata + episode labels).
        sidecars: set[str] = set()
        for path in context.raw_files:
            lowered = path.lower()
            if lowered.endswith(".csv"):
                sidecars.add(path)
            elif _RR_FILE_RE.search(Path(path).stem):
                sidecars.add(path)
        self._sidecars = sidecars

        self._labels_file_by_record = {}
        for path in context.raw_files:
            if not path.lower().endswith(".csv"):
                continue
            match = _ECG_LABELS_RE.search(Path(path).stem)
            if match:
                record_id = match.group("record_id").lower()
                self._labels_file_by_record[record_id] = path

        self._meta_by_record = self._load_metadata(context)
        self._episodes_by_record = {}

    def sidecar_files(self, context: AnnotationContext) -> set[str]:
        return set(self._sidecars)

    def enrich_recording(
        self,
        recording: ECGRecording,
        *,
        context: AnnotationContext,
        file_group: RecordingFileGroup,
        recording_index: int,
    ) -> None:
        del recording_index

        record_id, file_index = _parse_ecg_stem(file_group.primary_file)
        if record_id is None or file_index is None:
            raise AnnotationDrop(
                f"Unable to parse IRIDIA-AF record id/file index from {file_group.primary_file}",
                reason="unparseable_record_name",
                dropped_rows=1,
                dropped_recordings=1,
            )

        if self._sampling_rate_hz is not None and int(recording.sampling_rate) != self._sampling_rate_hz:
            recording.sampling_rate = self._sampling_rate_hz
            recording.duration_seconds = (
                float(recording.num_samples) / float(self._sampling_rate_hz)
                if self._sampling_rate_hz > 0
                else recording.duration_seconds
            )
        recording.units = "mV"

        intervals = self._episodes_for_record(context, record_id=record_id)
        rhythm_episodes = _build_rhythm_episodes(
            intervals,
            file_index=file_index,
            total_samples=int(recording.num_samples),
        )
        recording.annotations = ECGAnnotations(
            beat_positions=None,
            beat_labels=None,
            rhythm_episodes=rhythm_episodes,
        )

        meta = self._meta_by_record.get(record_id)
        if not recording.subject_id:
            recording.subject_id = (meta.patient_id if meta and meta.patient_id else record_id)
        if recording.age is None and meta is not None:
            recording.age = meta.age
        if recording.sex is None and meta is not None:
            recording.sex = meta.sex

        labels = dict(recording.labels or {})
        labels["iridia_af"] = {
            "record_id": record_id,
            "file_index": file_index,
            "episode_annotation_file": self._labels_file_by_record.get(record_id),
            "source_label_convention": self._SOURCE_LABEL_CONVENTION,
            "canonical_mapping_version": self._CANONICAL_MAPPING_VERSION,
            "unmapped_source_labels": [],
            "calibration_samples": self._calibration_samples,
            "af_present_from_rhythm": _episodes_have_af(rhythm_episodes),
        }
        recording.labels = labels

    def _episodes_for_record(self, context: AnnotationContext, *, record_id: str) -> list[_AFInterval]:
        cached = self._episodes_by_record.get(record_id)
        if cached is not None:
            return cached

        labels_path = self._labels_file_by_record.get(record_id)
        if labels_path is None:
            raise AnnotationDrop(
                f"Missing required IRIDIA-AF episode file for {record_id} (expected {record_id}_ecg_labels.csv)",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        intervals: list[_AFInterval] = []
        for row in context.read_csv_rows(labels_path):
            start_file = _opt_nonneg_int(row.get("start_file_index"))
            start_sample = _opt_nonneg_int(row.get("start_qrs_index"))
            end_file = _opt_nonneg_int(row.get("end_file_index"))
            end_sample = _opt_nonneg_int(row.get("end_qrs_index"))
            if None in (start_file, start_sample, end_file, end_sample):
                continue
            intervals.append(
                _AFInterval(
                    start_file_index=int(start_file),
                    start_sample=int(start_sample),
                    end_file_index=int(end_file),
                    end_sample=int(end_sample),
                )
            )

        self._episodes_by_record[record_id] = intervals
        return intervals

    def _load_metadata(self, context: AnnotationContext) -> dict[str, _SubjectMeta]:
        metadata_files = [
            p
            for p in context.raw_files
            if p.lower().endswith(".csv") and "metadata" in Path(p).name.lower()
        ]
        meta: dict[str, _SubjectMeta] = {}
        for rel_path in sorted(metadata_files):
            for row in context.read_csv_rows(rel_path):
                record_id = str(row.get("record_id") or "").strip().lower()
                if not record_id:
                    continue
                meta[record_id] = _SubjectMeta(
                    patient_id=_opt_text(row.get("patient_id")),
                    age=_parse_age(row.get("patient_age")),
                    sex=_parse_sex(row.get("patient_sex")),
                )
        return meta


def _is_ecg_signal_file(path: str) -> bool:
    lowered = path.lower()
    if not (lowered.endswith(".h5") or lowered.endswith(".hdf5")):
        return False
    return bool(_ECG_FILE_RE.search(Path(path).stem))


def _parse_ecg_stem(primary_file: str) -> tuple[str | None, int | None]:
    match = _ECG_FILE_RE.search(Path(primary_file).stem)
    if not match:
        return None, None
    return match.group("record_id").lower(), int(match.group("file_index"))


def _build_rhythm_episodes(
    intervals: list[_AFInterval],
    *,
    file_index: int,
    total_samples: int,
) -> list[RhythmEpisode] | None:
    if total_samples <= 0:
        return None

    af_spans: list[tuple[int, int]] = []
    for interval in intervals:
        span = _af_span_for_file(interval, file_index=file_index, total_samples=total_samples)
        if span is not None:
            af_spans.append(span)

    af_spans = _merge_spans(af_spans)

    episodes: list[RhythmEpisode] = []
    cursor = 0
    for start, end in af_spans:
        if start > cursor:
            episodes.append(
                RhythmEpisode(
                    start_sample=cursor,
                    end_sample=start,
                    rhythm_type="NSR",
                    original_label="(N",
                )
            )
        episodes.append(
            RhythmEpisode(
                start_sample=start,
                end_sample=end,
                rhythm_type="AF",
                original_label="(AFIB",
            )
        )
        cursor = end

    if cursor < total_samples:
        episodes.append(
            RhythmEpisode(
                start_sample=cursor,
                end_sample=total_samples,
                rhythm_type="NSR",
                original_label="(N",
            )
        )

    return episodes or None


def _af_span_for_file(
    interval: _AFInterval,
    *,
    file_index: int,
    total_samples: int,
) -> tuple[int, int] | None:
    starts_here = interval.start_file_index == file_index
    ends_here = interval.end_file_index == file_index
    spans_through = interval.start_file_index < file_index < interval.end_file_index

    if starts_here and ends_here:
        start, end = interval.start_sample, interval.end_sample
    elif starts_here:
        start, end = interval.start_sample, total_samples
    elif ends_here:
        start, end = 0, interval.end_sample
    elif spans_through:
        start, end = 0, total_samples
    else:
        return None

    start = max(0, min(start, total_samples))
    end = max(0, min(end, total_samples))
    if end <= start:
        return None
    return start, end


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _episodes_have_af(episodes: list[RhythmEpisode] | None) -> bool | None:
    if episodes is None:
        return None
    if not episodes:
        return False
    return any(ep.rhythm_type.upper() == "AF" for ep in episodes)


def _opt_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _opt_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _opt_nonneg_int(value: Any) -> int | None:
    text = _opt_text(value)
    if text is None:
        return None
    try:
        parsed = int(round(float(text)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_age(value: Any) -> int | None:
    text = _opt_text(value)
    if text is None:
        return None
    try:
        numeric = float(text)
    except ValueError:
        return None
    if numeric < 0:
        return None
    return int(round(numeric))


def _parse_sex(value: Any) -> str | None:
    text = _opt_text(value)
    if text is None:
        return None
    upper = text.upper()
    if upper in {"M", "MALE"}:
        return "M"
    if upper in {"F", "FEMALE"}:
        return "F"
    return None
