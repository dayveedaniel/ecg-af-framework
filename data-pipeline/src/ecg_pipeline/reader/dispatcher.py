from __future__ import annotations

from collections import OrderedDict

from ecg_pipeline.reader.models import RecordingFileGroup
from ecg_pipeline.reader.readers import BaseReader


class FormatDispatcher:
    def __init__(self, readers: list[BaseReader]):
        self.readers = readers

    def discover_and_group(self, file_list: list[str]) -> list[RecordingFileGroup]:
        grouped: OrderedDict[str, RecordingFileGroup] = OrderedDict()
        for reader in self.readers:
            for group in reader.discover_recordings(file_list):
                key = f"{group.detected_format}:{group.primary_file}"
                grouped[key] = group
        return list(grouped.values())

    def get_reader_for_group(self, group: RecordingFileGroup) -> BaseReader:
        for reader in self.readers:
            if reader.format_name == group.detected_format:
                return reader
        raise ValueError(f"No reader registered for format '{group.detected_format}'")
