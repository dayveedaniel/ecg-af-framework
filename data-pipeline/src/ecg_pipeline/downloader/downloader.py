from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone

from ecg_pipeline.config import PipelineConfig
from ecg_pipeline.downloader.file_filter import is_required_raw_file, prune_raw_prefix
from ecg_pipeline.downloader.models import DownloadFileResult, DownloadResult
from ecg_pipeline.downloader.strategies.base import BaseDownloadStrategy
from ecg_pipeline.downloader.strategies.direct_url import DirectURLStrategy
from ecg_pipeline.downloader.strategies.figshare import FigshareStrategy
from ecg_pipeline.downloader.strategies.physionet_s3 import PhysioNetS3Strategy
from ecg_pipeline.downloader.strategies.zenodo import ZenodoStrategy
from ecg_pipeline.manifest import DatasetManifest
from ecg_pipeline.utils.s3 import S3Client


class Downloader:
    """Downloads and (optionally) extracts a dataset based on its manifest."""

    def __init__(
        self, *, manifest: DatasetManifest, s3_client: S3Client, config: PipelineConfig
    ):
        self._manifest = manifest
        self._s3 = s3_client
        self._cfg = config

    def is_already_downloaded(self) -> bool:
        prefix = self._raw_prefix()
        for key in self._s3.iter_keys(self._cfg.s3_bucket, prefix):
            if key.endswith("/_download_report.json") or key.endswith(
                "_download_report.json"
            ):
                continue
            return True
        return False

    def run(
        self,
        *,
        force_redownload: bool = False,
        on_result: Callable[[DownloadFileResult], None] | None = None,
    ) -> DownloadResult:
        ts = datetime.now(timezone.utc)
        prefix = self._raw_prefix()

        if force_redownload:
            self._s3.delete_prefix(bucket=self._cfg.s3_bucket, prefix=prefix)

        if self.is_already_downloaded() and not force_redownload:
            prune_summary = prune_raw_prefix(
                s3_client=self._s3,
                bucket=self._cfg.s3_bucket,
                prefix=prefix,
            )
            report_key = f"{prefix}_download_report.json"
            self._s3.put_json(
                bucket=self._cfg.s3_bucket,
                key=report_key,
                obj={
                    "generated_at_epoch": time.time(),
                    "dataset": self._manifest.dataset.name,
                    "url": self._manifest.source.url,
                    "provider": self._manifest.provider,
                    "skipped": True,
                    "message": "raw prefix already contains files (use force_redownload to refetch)",
                    "summary": {
                        "ok_files": prune_summary.kept_files,
                        "deleted_non_required_files": prune_summary.deleted_non_required_files,
                        "deleted_empty_folder_markers": prune_summary.deleted_empty_folder_markers,
                    },
                },
            )
            return DownloadResult(
                dataset_name=self._manifest.dataset.name,
                s3_prefix=prefix,
                file_count=prune_summary.kept_files,
                total_size_bytes=0,
                download_timestamp=ts,
                skipped=True,
                report_key=report_key,
            )

        strategy = self._build_strategy()
        results: list[DownloadFileResult] = []
        total_size = 0

        for item in strategy.run():
            results.append(item)
            if on_result is not None:
                on_result(item)
            if (
                item.status == "ok"
                and item.size_bytes
                and item.file
                and is_required_raw_file(item.file)
            ):
                total_size += int(item.size_bytes)

        prune_summary = prune_raw_prefix(
            s3_client=self._s3,
            bucket=self._cfg.s3_bucket,
            prefix=prefix,
        )

        file_count = prune_summary.kept_files
        error_count = sum(1 for r in results if r.status == "error")

        report_key = f"{prefix}_download_report.json"
        self._s3.put_json(
            bucket=self._cfg.s3_bucket,
            key=report_key,
            obj={
                "generated_at_epoch": time.time(),
                "dataset": self._manifest.dataset.name,
                "url": self._manifest.source.url,
                "provider": self._manifest.provider,
                "results": [asdict(r) for r in results],
                "summary": {
                    "ok_files": file_count,
                    "error_files": error_count,
                    "total_size_bytes": total_size,
                    "deleted_non_required_files": prune_summary.deleted_non_required_files,
                    "deleted_empty_folder_markers": prune_summary.deleted_empty_folder_markers,
                },
            },
        )

        return DownloadResult(
            dataset_name=self._manifest.dataset.name,
            s3_prefix=prefix,
            file_count=file_count,
            total_size_bytes=total_size,
            download_timestamp=ts,
            skipped=False,
            report_key=report_key,
        )

    def _raw_prefix(self) -> str:
        return f"raw/{self._manifest.dataset.name}/"

    def _build_strategy(self) -> BaseDownloadStrategy:
        provider = self._manifest.provider
        common = dict(
            manifest=self._manifest,
            s3_client=self._s3,
            bucket=self._cfg.s3_bucket,
            raw_prefix=self._raw_prefix(),
            timeout_seconds=self._cfg.download_timeout_seconds,
            chunk_size_bytes=self._cfg.download_chunk_size_bytes,
        )

        if provider == "physionet_s3":
            return PhysioNetS3Strategy(
                physionet_bucket=self._cfg.physionet_s3_bucket, **common
            )
        if provider == "zenodo":
            return ZenodoStrategy(**common)
        if provider == "figshare":
            return FigshareStrategy(**common)
        if provider == "direct_url":
            return DirectURLStrategy(**common)

        raise ValueError(f"Unsupported provider: {provider}")
