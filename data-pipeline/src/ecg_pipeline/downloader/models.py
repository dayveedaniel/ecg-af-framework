from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DownloadFileResult:
    status: str  # "ok" | "error" | "skipped"
    file: str | None = None
    message: str | None = None
    url: str | None = None
    s3_key: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class DownloadResult:
    dataset_name: str
    s3_prefix: str
    file_count: int
    total_size_bytes: int
    download_timestamp: datetime
    skipped: bool = False
    report_key: str | None = None

