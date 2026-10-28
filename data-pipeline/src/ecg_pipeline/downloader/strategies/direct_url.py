from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from ecg_pipeline.downloader.models import DownloadFileResult
from ecg_pipeline.downloader.strategies._extract import extract_archive, resolve_archive_format
from ecg_pipeline.downloader.strategies._http import download_to_file, filename_from_url
from ecg_pipeline.downloader.strategies.base import BaseDownloadStrategy


class DirectURLStrategy(BaseDownloadStrategy):
    def run(self) -> Iterable[DownloadFileResult]:
        url = self.ctx.manifest.source.url
        name = filename_from_url(url)

        with tempfile.TemporaryDirectory(prefix="ecg-pipeline-download-") as tmp:
            tmp_dir = Path(tmp)
            local_path = tmp_dir / name

            try:
                size = download_to_file(
                    url,
                    local_path,
                    timeout_seconds=self.ctx.timeout_seconds,
                    chunk_size_bytes=self.ctx.chunk_size_bytes,
                )
            except Exception as exc:
                yield DownloadFileResult(status="error", file=name, url=url, message=str(exc))
                return

            archive_format = resolve_archive_format(local_path.name)
            if archive_format:
                extracted_dir = tmp_dir / "extracted"
                try:
                    extracted = extract_archive(local_path, archive_format=archive_format, output_dir=extracted_dir)
                except Exception as exc:
                    key = f"{self.ctx.raw_prefix}{name}"
                    self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=key, filename=local_path)
                    yield DownloadFileResult(
                        status="error",
                        file=name,
                        url=url,
                        message=f"archive extraction failed ({archive_format}): {exc} (uploaded original)",
                        s3_key=key,
                        size_bytes=size,
                    )
                    return

                for rel, path in extracted:
                    key = f"{self.ctx.raw_prefix}{rel}"
                    uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=key, filename=path)
                    yield DownloadFileResult(status="ok", file=rel, url=url, message="extracted", s3_key=uploaded.key)
                return

            key = f"{self.ctx.raw_prefix}{name}"
            uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=key, filename=local_path)
            yield DownloadFileResult(status="ok", file=name, url=url, message="downloaded", s3_key=uploaded.key, size_bytes=size)
