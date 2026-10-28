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
    header_global_class: str | None


@register_annotation_adapter("cpsc2021_paroxysmal_af_v1")
class CPSC2021Adapter(AnnotationAdapter):
    _CANONICAL_MAPPING_VERSION = "cpsc2021_rhythm_v1"
    _SOURCE_LABEL_CONVENTION = "wfdb_aux_note_token_and_header_comment"

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._annotation_extension = str(self.options.get("annotation_extension", "atr")).strip().lower()
        self._cache: dict[str, _ParsedRecordAnnotations] = {}

    def prepare(self, context: AnnotationContext) -> None:
        if not self._annotation_extension:
            raise ValueError("CPSC2021 adapter requires a non-empty annotation extension")
        ann_suffix = f".{self._annotation_extension}"
        ann_files = [path for path in context.raw_files if path.lower().endswith(ann_suffix)]
        if not ann_files:
            raise FileNotFoundError(
                f"CPSC2021 adapter found no '{ann_suffix}' files in raw dataset files"
            )
        self._cache = {}

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

        if not recording.subject_id:
            recording.subject_id = file_group.recording_base_name

        labels = dict(recording.labels or {})
        labels["cpsc2021"] = {
            "annotation_file": parsed.annotation_file,
            "annotation_extension": self._annotation_extension,
            "source_label_convention": self._SOURCE_LABEL_CONVENTION,
            "canonical_mapping_version": self._CANONICAL_MAPPING_VERSION,
            "unmapped_source_labels": list(parsed.unmapped_source_labels),
            "header_global_rhythm_class": parsed.header_global_class,
            "dataset_partition": _partition_from_record_base(record_base),
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
        header_global_class = _read_header_global_class(context=context, record_base=record_base)

        if not rhythm_episodes and header_global_class == "persistent atrial fibrillation":
            rhythm_episodes = [
                RhythmEpisode(
                    start_sample=0,
                    end_sample=total_samples,
                    rhythm_type="AF",
                    original_label="(AFIB",
                )
            ]
        elif rhythm_episodes is None and header_global_class == "non atrial fibrillation":
            rhythm_episodes = []
        elif rhythm_episodes is None and header_global_class == "paroxysmal atrial fibrillation":
            raise AnnotationDrop(
                f"Missing rhythm transition annotations in {annotation_file} for paroxysmal AF record",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        return _ParsedRecordAnnotations(
            beat_positions=beat_positions,
            beat_labels=beat_labels,
            rhythm_episodes=rhythm_episodes,
            annotation_file=annotation_file,
            unmapped_source_labels=sorted(set(unmapped_source_labels)),
            header_global_class=header_global_class,
        )


def _record_base_from_primary(primary_file: str) -> str:
    path = str(primary_file).strip()
    if path.lower().endswith(".hea"):
        return path[: -len(".hea")]
    return str(Path(path).with_suffix(""))


def _partition_from_record_base(record_base: str) -> str | None:
    parts = Path(record_base).parts
    if not parts:
        return None
    if len(parts) == 1:
        return None
    return parts[0]


def _read_header_global_class(*, context: AnnotationContext, record_base: str) -> str | None:
    rel_path = f"{record_base}.hea"
    if not context.has_file(rel_path):
        return None

    text = context.read_text(rel_path, encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        comment = stripped.lstrip("#").strip().lower()
        for candidate in (
            "non atrial fibrillation",
            "persistent atrial fibrillation",
            "paroxysmal atrial fibrillation",
        ):
            if candidate in comment:
                return candidate
    return None


def _read_wfdb_annotation(*, context: AnnotationContext, rel_path: str):
    if not context.has_file(rel_path):
        raise FileNotFoundError(f"WFDB annotation sidecar not found: {rel_path}")

    if "." not in rel_path:
        raise ValueError(f"Invalid WFDB annotation path '{rel_path}'")
    record_base, extension = rel_path.rsplit(".", 1)

    payload = context.read_bytes(rel_path)
    wfdb = _load_wfdb()
    with tempfile.TemporaryDirectory(prefix="ecg-cpsc2021-ann-") as tmp:
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

    mapping = {
        "AFIB": "AF",
        "AF": "AF",
        "ATRIALFIBRILLATION": "AF",
        "AFL": "AFL",
        "ATRIALFLUTTER": "AFL",
        "N": "NSR",
        "NSR": "NSR",
        "SINUS": "NSR",
        "SINUSRHYTHM": "NSR",
        "J": "OTHER",
        "AVJUNCTIONALRHYTHM": "OTHER",
    }
    canonical = mapping.get(token)
    if canonical is None:
        return "OTHER", token, False
    return canonical, token, True


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
            "CPSC2021 adapter requires dependency 'wfdb'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
