from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ecg_pipeline.annotations.context import AnnotationContext
from ecg_pipeline.reader.models import ECGRecording, RecordingFileGroup


class AnnotationDrop(Exception):
    def __init__(
        self,
        message: str,
        *,
        reason: str = "unspecified",
        dropped_rows: int = 0,
        dropped_recordings: int = 1,
    ) -> None:
        super().__init__(message)
        self.reason = str(reason or "unspecified").strip() or "unspecified"
        self.dropped_rows = max(0, int(dropped_rows))
        self.dropped_recordings = max(0, int(dropped_recordings))


class AnnotationAdapter(ABC):
    adapter_name: str = ""

    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = dict(options or {})

    def prepare(self, context: AnnotationContext) -> None:
        """Load and cache sidecars once per dataset."""

    def sidecar_files(self, context: AnnotationContext) -> set[str]:
        """Return raw dataset-relative files that should be excluded from signal discovery."""
        return set()

    def reader_options_for_group(self, *, file_group: RecordingFileGroup) -> dict[str, Any]:
        """Return reader options for a specific signal group."""
        return {}

    @abstractmethod
    def enrich_recording(
        self,
        recording: ECGRecording,
        *,
        context: AnnotationContext,
        file_group: RecordingFileGroup,
        recording_index: int,
    ) -> None:
        """Mutate recording in place with annotations/demographics/labels."""
        raise NotImplementedError
