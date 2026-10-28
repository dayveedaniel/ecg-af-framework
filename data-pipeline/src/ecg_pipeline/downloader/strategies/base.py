from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ecg_pipeline.downloader.models import DownloadFileResult
from ecg_pipeline.manifest import DatasetManifest
from ecg_pipeline.utils.s3 import S3Client

USER_AGENT = "ecg-pipeline-downloader/0.1"


@dataclass(frozen=True)
class StrategyContext:
    manifest: DatasetManifest
    s3_client: S3Client
    bucket: str
    raw_prefix: str
    timeout_seconds: int
    chunk_size_bytes: int


class BaseDownloadStrategy(ABC):
    def __init__(
        self,
        *,
        manifest: DatasetManifest,
        s3_client: S3Client,
        bucket: str,
        raw_prefix: str,
        timeout_seconds: int,
        chunk_size_bytes: int,
    ) -> None:
        self.ctx = StrategyContext(
            manifest=manifest,
            s3_client=s3_client,
            bucket=bucket,
            raw_prefix=raw_prefix,
            timeout_seconds=timeout_seconds,
            chunk_size_bytes=chunk_size_bytes,
        )

    @abstractmethod
    def run(self) -> Iterable[DownloadFileResult]:
        ...


def safe_join(base_dir: Path, relative_path: str) -> Path:
    candidate = (base_dir / relative_path).resolve()
    if not str(candidate).startswith(str(base_dir.resolve())):
        raise ValueError(f"Refusing to write outside extraction dir: {relative_path}")
    return candidate
