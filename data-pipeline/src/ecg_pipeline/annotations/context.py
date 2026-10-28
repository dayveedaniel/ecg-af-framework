from __future__ import annotations

import csv
import fnmatch
from dataclasses import dataclass, field
from typing import Any

from ecg_pipeline.manifest import DatasetManifest
from ecg_pipeline.utils.s3 import S3Client


@dataclass
class AnnotationContext:
    manifest: DatasetManifest
    dataset_name: str
    bucket: str
    raw_prefix: str
    raw_files: list[str]
    s3_client: S3Client
    _bytes_cache: dict[str, bytes] = field(default_factory=dict)
    _text_cache: dict[str, str] = field(default_factory=dict)
    _csv_cache: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    _raw_file_set: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self._raw_file_set = set(self.raw_files)

    def has_file(self, rel_path: str) -> bool:
        return rel_path in self._raw_file_set

    def find_files(self, pattern: str) -> list[str]:
        return sorted(fnmatch.filter(self.raw_files, pattern))

    def read_bytes(self, rel_path: str) -> bytes:
        if rel_path not in self._raw_file_set:
            raise FileNotFoundError(f"Annotation sidecar not found in raw dataset: {rel_path}")
        if rel_path not in self._bytes_cache:
            key = f"{self.raw_prefix}{rel_path}"
            self._bytes_cache[rel_path] = self.s3_client.get_bytes(bucket=self.bucket, key=key)
        return self._bytes_cache[rel_path]

    def read_text(self, rel_path: str, *, encoding: str = "utf-8") -> str:
        cache_key = f"{rel_path}:{encoding}"
        if cache_key not in self._text_cache:
            self._text_cache[cache_key] = self.read_bytes(rel_path).decode(encoding, errors="ignore")
        return self._text_cache[cache_key]

    def read_csv_rows(self, rel_path: str) -> list[dict[str, str]]:
        if rel_path not in self._csv_cache:
            text = self.read_text(rel_path, encoding="utf-8-sig")
            reader = csv.DictReader(text.splitlines())
            rows: list[dict[str, str]] = []
            for row in reader:
                rows.append({str(k): "" if v is None else str(v) for k, v in row.items() if k is not None})
            self._csv_cache[rel_path] = rows
        return self._csv_cache[rel_path]

    def option(self, key: str, default: Any = None) -> Any:
        return self.manifest.annotations.options.get(key, default)
