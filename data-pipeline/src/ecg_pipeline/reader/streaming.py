from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ecg_pipeline.downloader.models import DownloadFileResult
from ecg_pipeline.reader.models import ProcessingFailure, ProcessingResult, RecordingFileGroup
from ecg_pipeline.utils.s3 import S3Client

WFDB_REQUIRED_EXTS = {".hea", ".dat"}
WFDB_SUPPORTED_EXTS = {".hea", ".dat", ".atr", ".qrs"}


@dataclass
class WfdbStreamingProcessor:
    dataset_name: str
    bucket: str
    raw_prefix: str
    processed_prefix: str
    desired_rate: int
    parquet_compression: str
    s3_client: S3Client
    _seen_files: set[str] = field(default_factory=set)
    _processed_bases: set[str] = field(default_factory=set)
    _results: list[ProcessingResult] = field(default_factory=list)
    _failures: list[ProcessingFailure] = field(default_factory=list)

    def ingest_download_result(self, item: DownloadFileResult) -> None:
        if item.status != "ok" or not item.file:
            return
        rel = item.file
        ext = Path(rel).suffix.lower()
        if ext not in WFDB_SUPPORTED_EXTS:
            return

        self._seen_files.add(rel)
        base_key = _base_key(rel)
        self._maybe_process_base(base_key)

    def finalize(self) -> tuple[list[ProcessingResult], list[ProcessingFailure]]:
        all_bases = sorted({_base_key(path) for path in self._seen_files})
        for base_key in all_bases:
            self._maybe_process_base(base_key)
        for base_key in all_bases:
            if base_key in self._processed_bases:
                continue
            files = {Path(path).suffix.lower() for path in self._seen_files if _base_key(path) == base_key}
            if ".hea" in files or ".dat" in files:
                missing = sorted(WFDB_REQUIRED_EXTS - files)
                self._failures.append(
                    ProcessingFailure(
                        recording_name=Path(base_key).name,
                        primary_file=f"{base_key}.hea",
                        error=f"incomplete WFDB recording group; missing: {', '.join(missing)}",
                    )
                )
                self._processed_bases.add(base_key)
        return self._results, self._failures

    def has_activity(self) -> bool:
        return bool(self._seen_files)

    def _maybe_process_base(self, base_key: str) -> None:
        if base_key in self._processed_bases:
            return
        if not self._is_ready(base_key):
            return

        associated_files = sorted(path for path in self._seen_files if _base_key(path) == base_key)
        if not associated_files:
            return

        group = RecordingFileGroup(
            recording_base_name=Path(base_key).name,
            primary_file=f"{base_key}.hea",
            associated_files=associated_files,
            detected_format="wfdb",
        )

        self._failures.append(
            ProcessingFailure(
                recording_name=group.recording_base_name,
                primary_file=group.primary_file,
                error="WFDB streaming processor is disabled in the adapter-based annotation pipeline.",
            )
        )
        self._processed_bases.add(base_key)

    def _is_ready(self, base_key: str) -> bool:
        files = {Path(path).suffix.lower() for path in self._seen_files if _base_key(path) == base_key}
        return WFDB_REQUIRED_EXTS.issubset(files)


def _base_key(path: str) -> str:
    p = Path(path)
    return str(p.with_suffix(""))
