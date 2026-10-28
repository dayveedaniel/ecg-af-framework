from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from ecg_pipeline.downloader.models import DownloadFileResult
from ecg_pipeline.downloader.strategies._extract import extract_archive, resolve_archive_format
from ecg_pipeline.downloader.strategies._http import download_to_file, http_get_json
from ecg_pipeline.downloader.strategies.base import BaseDownloadStrategy
from ecg_pipeline.utils.helpers import zenodo_record_id


class ZenodoStrategy(BaseDownloadStrategy):
    def run(self) -> Iterable[DownloadFileResult]:
        url = self.ctx.manifest.source.url
        record_id = zenodo_record_id(url)
        if not record_id:
            yield DownloadFileResult(status="error", message="unable to parse Zenodo record ID", url=url)
            return

        api_url = f"https://zenodo.org/api/records/{record_id}"
        try:
            data = http_get_json(api_url, timeout_seconds=self.ctx.timeout_seconds)
        except Exception as exc:
            yield DownloadFileResult(status="error", message=f"Zenodo API failed: {exc}", url=api_url)
            return

        files = data.get("files", []) or []
        if not files:
            yield DownloadFileResult(status="error", message="no files in Zenodo record", url=url)
            return

        with tempfile.TemporaryDirectory(prefix="ecg-pipeline-zenodo-") as tmp:
            tmp_dir = Path(tmp)
            for item in files:
                key = item.get("key") or item.get("filename") or "file.bin"
                link = (item.get("links") or {}).get("self") or (item.get("links") or {}).get("download")
                if not link:
                    yield DownloadFileResult(status="error", file=key, url=url, message="missing file link")
                    continue

                local_path = tmp_dir / key
                try:
                    size = download_to_file(
                        link,
                        local_path,
                        timeout_seconds=self.ctx.timeout_seconds,
                        chunk_size_bytes=self.ctx.chunk_size_bytes,
                    )
                except Exception as exc:
                    yield DownloadFileResult(status="error", file=key, url=link, message=str(exc))
                    continue

                archive_format = resolve_archive_format(local_path.name)
                if archive_format:
                    extracted_dir = tmp_dir / "extracted" / key
                    try:
                        extracted = extract_archive(local_path, archive_format=archive_format, output_dir=extracted_dir)
                    except Exception as exc:
                        s3_key = f"{self.ctx.raw_prefix}{key}"
                        self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=s3_key, filename=local_path)
                        yield DownloadFileResult(
                            status="error",
                            file=key,
                            url=link,
                            message=f"archive extraction failed ({archive_format}): {exc} (uploaded original)",
                            s3_key=s3_key,
                            size_bytes=size,
                        )
                        continue

                    for rel, path in extracted:
                        s3_key = f"{self.ctx.raw_prefix}{rel}"
                        uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=s3_key, filename=path)
                        yield DownloadFileResult(status="ok", file=rel, url=link, message="extracted", s3_key=uploaded.key)
                    continue

                s3_key = f"{self.ctx.raw_prefix}{key}"
                uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=s3_key, filename=local_path)
                yield DownloadFileResult(
                    status="ok",
                    file=key,
                    url=link,
                    message="downloaded",
                    s3_key=uploaded.key,
                    size_bytes=size,
                )
