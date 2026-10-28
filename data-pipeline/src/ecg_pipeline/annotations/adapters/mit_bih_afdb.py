from __future__ import annotations

import importlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecg_pipeline.annotations.base import AnnotationAdapter
from ecg_pipeline.annotations.context import AnnotationContext
from ecg_pipeline.annotations.registry import register_annotation_adapter
from ecg_pipeline.reader.models import ECGAnnotations, ECGRecording, RecordingFileGroup, RhythmEpisode


@dataclass(frozen=True)
class _ParsedRecordAnnotations:
    beat_positions: list[int] | None
    beat_labels: list[str] | None
    rhythm_episodes: list[RhythmEpisode] | None
    rhythm_source_file: str | None
    beat_source_file: str | None
    beat_source_kind: str | None
    unmapped_source_labels: list[str]


@register_annotation_adapter("mit_bih_afdb_v1")
class MITBIHAFDBAdapter(AnnotationAdapter):
    _CANONICAL_MAPPING_VERSION = "mit_bih_afdb_rhythm_v1"
    _SOURCE_LABEL_CONVENTION = "wfdb_aux_note_token"

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._rhythm_extension = str(self.options.get("rhythm_annotation_extension", "atr")).strip().lower()
        self._corrected_beat_extension = str(
            self.options.get("corrected_beat_annotation_extension", "qrsc")
        ).strip().lower()
        self._automated_beat_extension = str(
            self.options.get("automated_beat_annotation_extension", self.options.get("beat_annotation_extension", "qrs"))
        ).strip().lower()
        self._cache: dict[str, _ParsedRecordAnnotations] = {}
        self._rhythm_files: set[str] = set()
        self._excluded_signal_files: set[str] = set()

    def prepare(self, context: AnnotationContext) -> None:
        if not self._rhythm_extension:
            raise ValueError("MIT-BIH AFDB adapter requires a non-empty rhythm annotation extension")
        if not self._corrected_beat_extension:
            raise ValueError("MIT-BIH AFDB adapter requires a non-empty corrected beat annotation extension")
        if not self._automated_beat_extension:
            raise ValueError("MIT-BIH AFDB adapter requires a non-empty automated beat annotation extension")
        if self._corrected_beat_extension == "atr" or self._automated_beat_extension == "atr":
            raise ValueError("MIT-BIH AFDB adapter does not allow '.atr' as a beat annotation extension")

        rhythm_suffix = f".{self._rhythm_extension}"
        self._rhythm_files = {path for path in context.raw_files if path.lower().endswith(rhythm_suffix)}
        if not self._rhythm_files:
            raise FileNotFoundError(
                f"MIT-BIH AFDB adapter found no '{rhythm_suffix}' files in raw dataset files"
            )

        self._excluded_signal_files = set()
        for hea_path in (path for path in context.raw_files if path.lower().endswith(".hea")):
            record_base = hea_path[: -len(".hea")]
            dat_path = f"{record_base}.dat"
            if context.has_file(dat_path):
                continue
            self._excluded_signal_files.update(
                path for path in context.raw_files if path.startswith(f"{record_base}.")
            )
        self._cache = {}

    def sidecar_files(self, context: AnnotationContext) -> set[str]:
        return set(self._excluded_signal_files)

    def enrich_recording(
        self,
        recording: ECGRecording,
        *,
        context: AnnotationContext,
        file_group: RecordingFileGroup,
        recording_index: int,
    ) -> None:
        record_base = _record_base_from_primary(file_group.primary_file)
        parsed = self._cache.get(record_base)
        if parsed is None:
            parsed = self._load_annotations_for_record(
                context=context,
                record_base=record_base,
                total_samples=int(recording.num_samples),
            )
            self._cache[record_base] = parsed

        has_annotations = parsed.beat_positions is not None or parsed.rhythm_episodes is not None
        recording.annotations = (
            ECGAnnotations(
                beat_positions=parsed.beat_positions,
                beat_labels=parsed.beat_labels,
                rhythm_episodes=parsed.rhythm_episodes,
            )
            if has_annotations
            else None
        )

        if not recording.subject_id:
            recording.subject_id = file_group.recording_base_name

        labels = dict(recording.labels or {})
        labels["mit_bih_afdb"] = {
            "rhythm_annotation_file": parsed.rhythm_source_file,
            "beat_annotation_file": parsed.beat_source_file,
            "beat_annotation_source_kind": parsed.beat_source_kind,
            "rhythm_annotation_extension": self._rhythm_extension,
            "source_label_convention": self._SOURCE_LABEL_CONVENTION,
            "canonical_mapping_version": self._CANONICAL_MAPPING_VERSION,
            "unmapped_source_labels": list(parsed.unmapped_source_labels),
            "preferred_beat_extensions": [
                ext
                for ext in [self._corrected_beat_extension, self._automated_beat_extension]
                if ext
            ],
            "af_present_from_rhythm": _episodes_have_af(parsed.rhythm_episodes),
        }
        recording.labels = labels

    def _load_annotations_for_record(
        self,
        *,
        context: AnnotationContext,
        record_base: str,
        total_samples: int,
    ) -> _ParsedRecordAnnotations:
        rhythm_file = f"{record_base}.{self._rhythm_extension}"
        if not context.has_file(rhythm_file):
            raise FileNotFoundError(f"MIT-BIH AFDB adapter missing rhythm annotation file: {rhythm_file}")
        rhythm_annotation = _read_wfdb_annotation(context=context, rel_path=rhythm_file)

        rhythm_markers, unmapped_source_labels = _extract_rhythm_markers(rhythm_annotation)
        rhythm_episodes = _build_rhythm_episodes(rhythm_markers, total_samples=total_samples)

        beat_annotation_file = None
        beat_annotation = None
        beat_source_kind = None
        for extension, source_kind in (
            (self._corrected_beat_extension, "corrected"),
            (self._automated_beat_extension, "automated"),
        ):
            if not extension:
                continue
            if beat_annotation_file is not None:
                break
            candidate = f"{record_base}.{extension}"
            if not context.has_file(candidate):
                continue
            beat_annotation_file = candidate
            beat_source_kind = source_kind
            beat_annotation = _read_wfdb_annotation(context=context, rel_path=candidate)

        beat_positions, beat_labels = _extract_beats(beat_annotation)
        return _ParsedRecordAnnotations(
            beat_positions=beat_positions,
            beat_labels=beat_labels,
            rhythm_episodes=rhythm_episodes,
            rhythm_source_file=rhythm_file,
            beat_source_file=beat_annotation_file,
            beat_source_kind=beat_source_kind,
            unmapped_source_labels=sorted(set(unmapped_source_labels)),
        )


def _record_base_from_primary(primary_file: str) -> str:
    path = str(primary_file).strip()
    if path.lower().endswith(".hea"):
        return path[: -len(".hea")]
    return str(Path(path).with_suffix(""))


def _read_wfdb_annotation(*, context: AnnotationContext, rel_path: str):
    if not context.has_file(rel_path):
        raise FileNotFoundError(f"WFDB annotation sidecar not found: {rel_path}")

    if "." not in rel_path:
        raise ValueError(f"Invalid WFDB annotation path '{rel_path}'")
    record_base, extension = rel_path.rsplit(".", 1)

    payload = context.read_bytes(rel_path)
    wfdb = _load_wfdb()
    with tempfile.TemporaryDirectory(prefix="ecg-afdb-ann-") as tmp:
        local_base = Path(tmp) / record_base
        local_base.parent.mkdir(parents=True, exist_ok=True)
        local_ann_path = Path(f"{local_base}.{extension}")
        local_ann_path.write_bytes(payload)
        return wfdb.rdann(str(local_base), extension)


def _extract_rhythm_markers(annotation) -> tuple[list[tuple[int, str, str]], list[str]]:
    samples = _annotation_field_to_list(annotation, "sample")
    symbols = _annotation_field_to_list(annotation, "symbol")
    aux_notes = _annotation_field_to_list(annotation, "aux_note")

    markers: list[tuple[int, str, str]] = []
    unmapped_source_labels: list[str] = []
    for idx, raw_sample in enumerate(samples):
        sample = int(raw_sample)
        if sample < 0:
            continue

        symbol = str(symbols[idx]).strip() if idx < len(symbols) else ""
        raw_note = _clean_aux_note(aux_notes[idx] if idx < len(aux_notes) else None)
        if raw_note is None:
            continue
        if symbol != "+" and not raw_note.startswith("("):
            continue

        canonical, source_token, mapped = _normalize_rhythm_label(raw_note)
        if canonical == "UNKNOWN":
            continue
        if not mapped:
            unmapped_source_labels.append(source_token)
        markers.append((sample, canonical, raw_note))

    return markers, unmapped_source_labels


def _build_rhythm_episodes(
    markers: list[tuple[int, str, str]],
    *,
    total_samples: int,
) -> list[RhythmEpisode] | None:
    if not markers:
        return None

    episodes: list[RhythmEpisode] = []
    sorted_markers = sorted(markers, key=lambda item: item[0])
    for idx, (start, rhythm_type, original_label) in enumerate(sorted_markers):
        end = sorted_markers[idx + 1][0] if idx + 1 < len(sorted_markers) else total_samples
        if end <= start:
            continue

        if episodes and episodes[-1].rhythm_type == rhythm_type and episodes[-1].end_sample >= start:
            prev = episodes.pop()
            episodes.append(
                RhythmEpisode(
                    start_sample=prev.start_sample,
                    end_sample=end,
                    rhythm_type=rhythm_type,
                    original_label=prev.original_label,
                )
            )
            continue

        episodes.append(
            RhythmEpisode(
                start_sample=start,
                end_sample=end,
                rhythm_type=rhythm_type,
                original_label=original_label,
            )
        )

    return episodes or None


def _extract_beats(annotation) -> tuple[list[int] | None, list[str] | None]:
    if annotation is None:
        return None, None

    samples = _annotation_field_to_list(annotation, "sample")
    symbols = _annotation_field_to_list(annotation, "symbol")
    aux_notes = _annotation_field_to_list(annotation, "aux_note")

    beat_positions: list[int] = []
    beat_labels: list[str] = []
    for idx, raw_sample in enumerate(samples):
        sample = int(raw_sample)
        if sample < 0:
            continue

        symbol = str(symbols[idx]).strip() if idx < len(symbols) else ""
        raw_note = _clean_aux_note(aux_notes[idx] if idx < len(aux_notes) else None)
        if symbol == "+":
            continue
        if raw_note and raw_note.startswith("("):
            continue

        beat_positions.append(sample)
        beat_labels.append(symbol or "N")

    if not beat_positions:
        return None, None
    labels = beat_labels if any(x.strip() for x in beat_labels) else None
    return beat_positions, labels


def _annotation_field_to_list(annotation: Any, field_name: str) -> list[Any]:
    value = getattr(annotation, field_name, None)
    if value is None:
        return []
    return list(value)


def _clean_aux_note(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    return text or None


def _normalize_rhythm_label(raw_note: str) -> tuple[str, str, bool]:
    note = str(raw_note).strip()
    if note.startswith("("):
        note = note[1:]
    token = "".join(ch for ch in note.upper() if ch.isalnum())
    if not token:
        return "UNKNOWN", token, False

    # Canonical label set for training-time rhythm semantics.
    mapping = {
        "AFIB": "AF",
        "AF": "AF",
        "ATRIALFIBRILLATION": "AF",
        "AFL": "AFL",
        "ATRIALFLUTTER": "AFL",
        "J": "OTHER",
        "AVJUNCTIONALRHYTHM": "OTHER",
        "N": "NSR",
        "NSR": "NSR",
        "SINUS": "NSR",
        "SINUSRHYTHM": "NSR",
    }
    canonical = mapping.get(token)
    if canonical is None:
        return "OTHER", token, False
    return canonical, token, True


def _episodes_have_af(episodes: list[RhythmEpisode] | None) -> bool | None:
    if not episodes:
        return None
    return any(ep.rhythm_type.upper() == "AF" for ep in episodes)


def _load_wfdb():
    try:
        return importlib.import_module("wfdb")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MIT-BIH AFDB adapter requires dependency 'wfdb'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
