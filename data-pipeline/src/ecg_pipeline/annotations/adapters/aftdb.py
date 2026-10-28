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
    beat_annotation_file: str


@register_annotation_adapter("aftdb_v1")
class AFTDBAdapter(AnnotationAdapter):
    _CANONICAL_MAPPING_VERSION = "aftdb_rhythm_v1"
    _SOURCE_LABEL_CONVENTION = "dataset_record_semantics_and_wfdb_qrs"

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._beat_extension = str(self.options.get("beat_annotation_extension", "qrs")).strip().lower()
        self._cache: dict[str, _ParsedRecordAnnotations] = {}
        self._sidecars: set[str] = set()

    def prepare(self, context: AnnotationContext) -> None:
        if not self._beat_extension:
            raise ValueError("AFTDB adapter requires a non-empty beat annotation extension")

        beat_suffix = f".{self._beat_extension}"
        beat_files = [path for path in context.raw_files if path.lower().endswith(beat_suffix)]
        if not beat_files:
            raise FileNotFoundError(
                f"AFTDB adapter found no '{beat_suffix}' files in raw dataset files"
            )

        self._sidecars = {path for path in context.raw_files if Path(path).name.upper() in {"RECORDS", "ANNOTATORS"}}
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

        recording.annotations = ECGAnnotations(
            beat_positions=parsed.beat_positions,
            beat_labels=parsed.beat_labels,
            rhythm_episodes=parsed.rhythm_episodes,
        )

        if not recording.subject_id:
            recording.subject_id = _subject_id_from_record_name(file_group.recording_base_name)

        challenge_split, challenge_group = _challenge_context_from_record_base(record_base)
        labels = dict(recording.labels or {})
        labels["aftdb"] = {
            "beat_annotation_file": parsed.beat_annotation_file,
            "beat_annotation_extension": self._beat_extension,
            "rhythm_annotation_source": "synthetic_full_record_af",
            "source_label_convention": self._SOURCE_LABEL_CONVENTION,
            "canonical_mapping_version": self._CANONICAL_MAPPING_VERSION,
            "unmapped_source_labels": [],
            "challenge_split": challenge_split,
            "challenge_group": challenge_group,
            "af_present_from_rhythm": True,
        }
        recording.labels = labels

    def _load_annotations_for_record(
        self,
        *,
        context: AnnotationContext,
        record_base: str,
        total_samples: int,
    ) -> _ParsedRecordAnnotations:
        beat_annotation_file = f"{record_base}.{self._beat_extension}"
        if not context.has_file(beat_annotation_file):
            raise AnnotationDrop(
                f"Missing required beat annotation file: {beat_annotation_file}",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        beat_annotation = _read_wfdb_annotation(context=context, rel_path=beat_annotation_file)
        beat_positions, beat_labels = _extract_beats(beat_annotation)
        if beat_positions is None:
            raise AnnotationDrop(
                f"Missing required beat annotations in: {beat_annotation_file}",
                reason="missing_required_annotation",
                dropped_rows=1,
                dropped_recordings=1,
            )

        rhythm_episodes = [
            RhythmEpisode(
                start_sample=0,
                end_sample=total_samples,
                rhythm_type="AF",
                original_label="(AFIB",
            )
        ]

        return _ParsedRecordAnnotations(
            beat_positions=beat_positions,
            beat_labels=beat_labels,
            rhythm_episodes=rhythm_episodes,
            beat_annotation_file=beat_annotation_file,
        )


def _record_base_from_primary(primary_file: str) -> str:
    path = str(primary_file).strip()
    if path.lower().endswith(".hea"):
        return path[: -len(".hea")]
    return str(Path(path).with_suffix(""))


def _subject_id_from_record_name(record_name: str) -> str:
    stem = str(record_name).strip()
    if len(stem) >= 2 and stem[0].isalpha() and stem[1:].isdigit():
        return stem[1:]
    return stem


def _challenge_context_from_record_base(record_base: str) -> tuple[str | None, str | None]:
    path = Path(record_base)
    split = path.parent.name if path.parent.name else None
    prefix = path.name[:1].lower()
    group_map = {
        "n": "non_terminating_af",
        "s": "terminating_af_long",
        "t": "terminating_af_short",
        "a": "test_set_a",
        "b": "test_set_b",
    }
    return split, group_map.get(prefix)


def _read_wfdb_annotation(*, context: AnnotationContext, rel_path: str):
    if not context.has_file(rel_path):
        raise FileNotFoundError(f"WFDB annotation sidecar not found: {rel_path}")

    if "." not in rel_path:
        raise ValueError(f"Invalid WFDB annotation path '{rel_path}'")
    record_base, extension = rel_path.rsplit(".", 1)

    payload = context.read_bytes(rel_path)
    wfdb = _load_wfdb()
    with tempfile.TemporaryDirectory(prefix="ecg-aftdb-ann-") as tmp:
        local_base = Path(tmp) / record_base
        local_base.parent.mkdir(parents=True, exist_ok=True)
        local_ann_path = Path(f"{local_base}.{extension}")
        local_ann_path.write_bytes(payload)
        return wfdb.rdann(str(local_base), extension)


def _extract_beats(annotation) -> tuple[list[int] | None, list[str] | None]:
    if annotation is None:
        return None, None

    samples = _annotation_field_to_list(annotation, "sample")
    symbols = _annotation_field_to_list(annotation, "symbol")
    beat_positions: list[int] = []
    beat_labels: list[str] = []

    for idx, raw_sample in enumerate(samples):
        sample = int(raw_sample)
        if sample < 0:
            continue

        symbol = str(symbols[idx]).strip() if idx < len(symbols) else ""
        if symbol == "+":
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


def _load_wfdb():
    try:
        return importlib.import_module("wfdb")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "AFTDB adapter requires dependency 'wfdb'. Install project dependencies (e.g. `uv sync`)."
        ) from exc
