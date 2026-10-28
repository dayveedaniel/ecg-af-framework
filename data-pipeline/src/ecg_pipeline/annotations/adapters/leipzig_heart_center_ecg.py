from __future__ import annotations

import importlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecg_pipeline.annotations.base import AnnotationAdapter, AnnotationDrop
from ecg_pipeline.annotations.context import AnnotationContext
from ecg_pipeline.annotations.registry import register_annotation_adapter
from ecg_pipeline.reader.models import ECGAnnotations, ECGRecording, RecordingFileGroup, RhythmEpisode


@dataclass(frozen=True)
class _ParsedRecordAnnotations:
    beat_positions: list[int] | None
    beat_labels: list[str] | None
    rhythm_episodes: list[RhythmEpisode] | None
    annotation_file: str
    unmapped_source_labels: list[str]


@dataclass(frozen=True)
class _SubjectMeta:
    subject_id: str | None
    age: int | None
    sex: str | None
    diagnosis: str | None
    ap_location: str | None
    source_file: str


@register_annotation_adapter("leipzig_heart_center_ecg_v1")
class LeipzigHeartCenterECGAdapter(AnnotationAdapter):
    _CANONICAL_MAPPING_VERSION = "leipzig_lch_rhythm_v1"
    _SOURCE_LABEL_CONVENTION = "wfdb_aux_note_token"
    _ADULTS_INFO_FILE = "adults-subject-info.csv"
    _CHILDREN_INFO_FILE = "children-subject-info.csv"
    _KNOWN_SIDECARS = {
        "adults-subject-info.csv",
        "children-subject-info.csv",
        "dataset_info.csv",
        "dataset_info_data_dictionary.csv",
        "subject_data_dictionary.csv",
    }

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._annotation_extension = str(self.options.get("annotation_extension", "atr")).strip().lower()
        self._cache: dict[str, _ParsedRecordAnnotations] = {}
        self._subject_meta_by_file: dict[str, _SubjectMeta] = {}
        self._sidecars: set[str] = set()

    def prepare(self, context: AnnotationContext) -> None:
        if not self._annotation_extension:
            raise ValueError("Leipzig adapter requires a non-empty annotation extension")
        ann_suffix = f".{self._annotation_extension}"
        ann_files = [path for path in context.raw_files if path.lower().endswith(ann_suffix)]
        if not ann_files:
            raise FileNotFoundError(
                f"Leipzig adapter found no '{ann_suffix}' files in raw dataset files"
            )

        missing_subject_files = [
            rel
            for rel in (self._ADULTS_INFO_FILE, self._CHILDREN_INFO_FILE)
            if not context.has_file(rel)
        ]
        if missing_subject_files:
            joined = ", ".join(missing_subject_files)
            raise FileNotFoundError(f"Leipzig adapter missing required demographics files: {joined}")

        self._subject_meta_by_file = {}
        self._load_subject_rows(context, rel_path=self._ADULTS_INFO_FILE)
        self._load_subject_rows(context, rel_path=self._CHILDREN_INFO_FILE)

        self._sidecars = {path for path in context.raw_files if path in self._KNOWN_SIDECARS}
        self._cache = {}

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

        meta = self._subject_meta_by_file.get(file_group.recording_base_name)
        if meta is not None:
            if recording.subject_id is None and meta.subject_id is not None:
                recording.subject_id = meta.subject_id
            if recording.age is None:
                recording.age = meta.age
            if recording.sex is None:
                recording.sex = meta.sex
        if not recording.subject_id:
            recording.subject_id = file_group.recording_base_name

        labels = dict(recording.labels or {})
        labels["leipzig_heart_center_ecg"] = {
            "annotation_file": parsed.annotation_file,
            "annotation_extension": self._annotation_extension,
            "source_label_convention": self._SOURCE_LABEL_CONVENTION,
            "canonical_mapping_version": self._CANONICAL_MAPPING_VERSION,
            "unmapped_source_labels": list(parsed.unmapped_source_labels),
            "af_present_from_rhythm": _episodes_have_af(parsed.rhythm_episodes),
            "demographics_source_file": meta.source_file if meta is not None else None,
            "diagnosis": meta.diagnosis if meta is not None else None,
            "ap_location": meta.ap_location if meta is not None else None,
        }
        recording.labels = labels

    def _load_subject_rows(self, context: AnnotationContext, *, rel_path: str) -> None:
        rows = context.read_csv_rows(rel_path)
        for row in rows:
            file_name = str(row.get("file_name") or "").strip()
            if not file_name:
                continue
            subject_meta = _SubjectMeta(
                subject_id=_opt_text(row.get("subject_id")),
                age=_parse_age(row.get("age")),
                sex=_parse_sex(row.get("gender")),
                diagnosis=_opt_text(row.get("diagnosis")),
                ap_location=_opt_text(row.get("ap_location")),
                source_file=rel_path,
            )
            if file_name in self._subject_meta_by_file:
                raise ValueError(
                    f"Leipzig demographics has duplicate file_name '{file_name}' across subject info files"
                )
            self._subject_meta_by_file[file_name] = subject_meta

    def _load_annotations_for_record(
        self,
        *,
        context: AnnotationContext,
        record_base: str,
        total_samples: int,
    ) -> _ParsedRecordAnnotations:
        annotation_file = f"{record_base}.{self._annotation_extension}"
        if not context.has_file(annotation_file):
            raise AnnotationDrop(
                f"Missing required annotation file: {annotation_file}",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        annotation = _read_wfdb_annotation(context=context, rel_path=annotation_file)
        rhythm_markers, unmapped_source_labels = _extract_rhythm_markers(annotation)
        rhythm_episodes = _build_rhythm_episodes(rhythm_markers, total_samples=total_samples)
        beat_positions, beat_labels = _extract_beats(annotation)

        return _ParsedRecordAnnotations(
            beat_positions=beat_positions,
            beat_labels=beat_labels,
            rhythm_episodes=rhythm_episodes,
            annotation_file=annotation_file,
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
    with tempfile.TemporaryDirectory(prefix="ecg-leipzig-ann-") as tmp:
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
    source_token = note.upper()
    if not source_token:
        return "UNKNOWN", source_token, False

    normalized = source_token.replace(" ", "")
    mapping = {
        "N": "NSR",
        "NSR": "NSR",
        "SINUS": "NSR",
        "SINUSRHYTHM": "NSR",
        "AFIB": "AF",
        "AF": "AF",
        "ATRIALFIBRILLATION": "AF",
        "AFL": "AFL",
        "ATRIALFLUTTER": "AFL",
        "VT": "OTHER",
        "IVR": "OTHER",
        "AVRT": "OTHER",
        "AVNRT": "OTHER",
        "EAT": "OTHER",
        "A": "OTHER",
        "B": "OTHER",
        "J": "OTHER",
        "/A": "OTHER",
        "/V": "OTHER",
    }

    canonical = mapping.get(normalized)
    if canonical is not None:
        return canonical, normalized, True

    collapsed = "".join(ch for ch in normalized if ch.isalnum())
    canonical = mapping.get(collapsed)
    if canonical is not None:
        return canonical, collapsed, True

    return "OTHER", normalized, False


def _opt_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


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


def _episodes_have_af(episodes: list[RhythmEpisode] | None) -> bool | None:
    if episodes is None:
        return None
    if not episodes:
        return False
    return any(ep.rhythm_type.upper() == "AF" for ep in episodes)


def _load_wfdb():
    try:
        return importlib.import_module("wfdb")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Leipzig adapter requires dependency 'wfdb'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
