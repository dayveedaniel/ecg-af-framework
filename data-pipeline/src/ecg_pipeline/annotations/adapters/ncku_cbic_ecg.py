from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecg_pipeline.annotations.base import AnnotationAdapter, AnnotationDrop
from ecg_pipeline.annotations.context import AnnotationContext
from ecg_pipeline.annotations.registry import register_annotation_adapter
from ecg_pipeline.reader.models import ECGAnnotations, ECGRecording, RecordingFileGroup, RhythmEpisode

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_SUBJECT_DIR_RE = re.compile(r"^(?P<subject>\d+)_(?P<time>\d+)$")

# Documented arrhythmia label codes (figshare description).
_BEAT_TOKEN_BY_CODE: dict[int, str] = {
    0: "N",
    1: "AFIB",
    2: "SVT",
    3: "PVC",
    4: "APC",
    5: "MA",
    6: "BW",
    7: "AVB1",
    8: "AFL",
}

# Rhythm-defining codes mapped to canonical rhythm classes. Codes absent here
# (normal, ectopic beats, noise) do not define a rhythm episode on their own.
_RHYTHM_CANONICAL_BY_CODE: dict[int, str] = {
    1: "AF",
    8: "AFL",
    2: "OTHER",
    7: "OTHER",
}

# Documented per-subject demographics (figshare description).
_SUBJECT_DEMOGRAPHICS: dict[str, tuple[str, int]] = {
    "1": ("M", 61),
    "2": ("F", 77),
    "3": ("M", 63),
    "4": ("M", 64),
    "5": ("M", 24),
    "6": ("M", 64),
}


@dataclass(frozen=True)
class _ParsedBeats:
    beat_positions: list[int]
    beat_labels: list[str]
    rhythm_episodes: list[RhythmEpisode]
    position_file: str
    label_file: str
    num_positions: int
    num_labels: int
    unmapped_source_labels: list[str]


@register_annotation_adapter("ncku_cbic_ecg_v1")
class NCKUCBICECGAdapter(AnnotationAdapter):
    _CANONICAL_MAPPING_VERSION = "ncku_cbic_rhythm_v1"
    _SOURCE_LABEL_CONVENTION = "ncku_cbic_per_rpeak_arrhythmia_code"

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._sampling_rate_hz = _opt_positive_int(self.options.get("sampling_rate_hz"))
        self._signal_filename = str(self.options.get("signal_filename", "OUTPUT_ECG_data.csv")).strip()
        self._position_filename = str(
            self.options.get("peak_position_filename", "OUTPUT_peak_position.csv")
        ).strip()
        self._label_filename = str(self.options.get("peak_label_filename", "OUTPUT_peak_label.csv")).strip()
        self._sidecars: set[str] = set()

    def prepare(self, context: AnnotationContext) -> None:
        if not self._signal_filename:
            raise ValueError("NCKU CBIC adapter requires a non-empty signal_filename option")

        signal_files = [p for p in context.raw_files if Path(p).name == self._signal_filename]
        if not signal_files:
            raise FileNotFoundError(
                f"NCKU CBIC adapter found no '{self._signal_filename}' signal files in raw dataset files"
            )

        # Only the ECG signal CSV is a waveform; every other CSV (per-peak labels
        # and positions, plus any stray tabular file) is excluded from discovery.
        self._sidecars = {
            p
            for p in context.raw_files
            if p.lower().endswith(".csv") and Path(p).name != self._signal_filename
        }

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

        primary = Path(file_group.primary_file)
        segment_dir = primary.parent
        segment_name = segment_dir.name or file_group.recording_base_name
        subject_id, measurement_time = _parse_segment_name(segment_name)

        # ECG segments are all named OUTPUT_ECG_data.csv, so rebuild a unique
        # recording identity from the owning '<subject>_<time>' directory.
        dataset_prefix = recording.recording_id.rsplit("/", 1)[0]
        recording.recording_name = segment_name
        recording.recording_id = f"{dataset_prefix}/{segment_name}"

        if self._sampling_rate_hz is not None and int(recording.sampling_rate) != self._sampling_rate_hz:
            recording.sampling_rate = self._sampling_rate_hz
            recording.duration_seconds = (
                float(recording.num_samples) / float(self._sampling_rate_hz)
                if self._sampling_rate_hz > 0
                else recording.duration_seconds
            )

        parsed = self._parse_beats(
            context,
            segment_dir=segment_dir,
            total_samples=int(recording.num_samples),
        )

        recording.annotations = ECGAnnotations(
            beat_positions=parsed.beat_positions or None,
            beat_labels=parsed.beat_labels or None,
            rhythm_episodes=parsed.rhythm_episodes,
        )

        if not recording.subject_id:
            recording.subject_id = f"subject_{subject_id}" if subject_id else segment_name

        demographics = _SUBJECT_DEMOGRAPHICS.get(subject_id or "")
        if demographics is not None:
            sex, age = demographics
            if recording.sex is None:
                recording.sex = sex
            if recording.age is None:
                recording.age = age

        labels = dict(recording.labels or {})
        labels["ncku_cbic_ecg"] = {
            "subject_number": subject_id,
            "measurement_time": measurement_time,
            "segment_directory": segment_name,
            "peak_position_file": parsed.position_file,
            "peak_label_file": parsed.label_file,
            "num_peak_positions": parsed.num_positions,
            "num_peak_labels": parsed.num_labels,
            "signal_units": "0.1V",
            "source_label_convention": self._SOURCE_LABEL_CONVENTION,
            "canonical_mapping_version": self._CANONICAL_MAPPING_VERSION,
            "unmapped_source_labels": list(parsed.unmapped_source_labels),
            "af_present_from_rhythm": _episodes_have_af(parsed.rhythm_episodes),
        }
        recording.labels = labels

    def _parse_beats(
        self,
        context: AnnotationContext,
        *,
        segment_dir: Path,
        total_samples: int,
    ) -> _ParsedBeats:
        position_path = _sibling(segment_dir, self._position_filename)
        label_path = _sibling(segment_dir, self._label_filename)

        if not context.has_file(position_path) or not context.has_file(label_path):
            raise AnnotationDrop(
                f"Missing required NCKU CBIC peak files in {segment_dir.as_posix()} "
                f"(expected {self._position_filename} and {self._label_filename})",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        positions = [int(round(v)) for v in _read_numbers(context, position_path)]
        codes = [int(round(v)) for v in _read_numbers(context, label_path)]
        num_positions = len(positions)
        num_labels = len(codes)

        count = min(num_positions, num_labels)
        if count == 0:
            raise AnnotationDrop(
                f"NCKU CBIC peak files contain no usable R-peak entries in {segment_dir.as_posix()}",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        beat_positions: list[int] = []
        beat_labels: list[str] = []
        rhythm_codes: list[int | None] = []
        unmapped: set[str] = set()
        for idx in range(count):
            sample = positions[idx]
            if sample < 0 or sample > total_samples:
                continue
            code = codes[idx]
            token = _BEAT_TOKEN_BY_CODE.get(code)
            if token is None:
                token = str(code)
                unmapped.add(token)
            beat_positions.append(sample)
            beat_labels.append(token)
            rhythm_codes.append(_RHYTHM_CANONICAL_BY_CODE.get(code))

        rhythm_episodes = _build_rhythm_episodes(beat_positions, rhythm_codes, total_samples=total_samples)

        return _ParsedBeats(
            beat_positions=beat_positions,
            beat_labels=beat_labels,
            rhythm_episodes=rhythm_episodes,
            position_file=position_path,
            label_file=label_path,
            num_positions=num_positions,
            num_labels=num_labels,
            unmapped_source_labels=sorted(unmapped),
        )


def _build_rhythm_episodes(
    beat_positions: list[int],
    rhythm_codes: list[int | None],
    *,
    total_samples: int,
) -> list[RhythmEpisode]:
    episodes: list[RhythmEpisode] = []
    run_start_idx: int | None = None
    run_label: str | None = None

    def _flush(run_label: str, start_idx: int, end_idx: int) -> None:
        start_sample = beat_positions[start_idx]
        last_sample = beat_positions[end_idx]
        end_sample = min(last_sample + 1, total_samples)
        if end_sample <= start_sample:
            end_sample = min(start_sample + 1, total_samples)
        if end_sample <= start_sample:
            return
        episodes.append(
            RhythmEpisode(
                start_sample=start_sample,
                end_sample=end_sample,
                rhythm_type=run_label,
                original_label=_original_label_for(run_label),
            )
        )

    for idx, canonical in enumerate(rhythm_codes):
        if canonical != run_label:
            if run_label is not None and run_start_idx is not None:
                _flush(run_label, run_start_idx, idx - 1)
            run_label = canonical
            run_start_idx = idx if canonical is not None else None

    if run_label is not None and run_start_idx is not None:
        _flush(run_label, run_start_idx, len(rhythm_codes) - 1)

    return episodes


def _original_label_for(canonical: str) -> str:
    if canonical == "AF":
        return "(AFIB"
    if canonical == "AFL":
        return "(AFL"
    return f"({canonical}"


def _parse_segment_name(segment_name: str) -> tuple[str | None, str | None]:
    match = _SUBJECT_DIR_RE.match(str(segment_name).strip())
    if not match:
        return None, None
    return match.group("subject"), match.group("time")


def _sibling(segment_dir: Path, filename: str) -> str:
    return (segment_dir / filename).as_posix()


def _read_numbers(context: AnnotationContext, rel_path: str) -> list[float]:
    text = context.read_text(rel_path, encoding="utf-8-sig")
    return [float(token) for token in _NUMBER_RE.findall(text)]


def _episodes_have_af(episodes: list[RhythmEpisode] | None) -> bool | None:
    if episodes is None:
        return None
    if not episodes:
        return False
    return any(ep.rhythm_type.upper() == "AF" for ep in episodes)


def _opt_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
