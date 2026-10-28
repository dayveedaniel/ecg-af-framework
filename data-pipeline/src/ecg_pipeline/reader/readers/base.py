from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ecg_pipeline.reader.models import ECGRecording, RecordingFileGroup


class BaseReader(ABC):
    format_name: str

    @abstractmethod
    def can_read(self, filepath: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def discover_recordings(self, file_list: list[str]) -> list[RecordingFileGroup]:
        raise NotImplementedError

    @abstractmethod
    def read_local_many(
        self,
        *,
        file_group: RecordingFileGroup,
        local_dataset_dir: Path,
        dataset_name: str,
        reader_options: dict[str, Any] | None = None,
    ) -> list[ECGRecording]:
        raise NotImplementedError
