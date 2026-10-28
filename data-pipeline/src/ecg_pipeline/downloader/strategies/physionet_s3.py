from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from ecg_pipeline.downloader.models import DownloadFileResult
from ecg_pipeline.downloader.strategies.base import BaseDownloadStrategy
from ecg_pipeline.utils.helpers import parse_physionet_dataset_base


class PhysioNetS3Strategy(BaseDownloadStrategy):
    def __init__(self, *, physionet_bucket: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._physionet_bucket = physionet_bucket

    def run(self) -> Iterable[DownloadFileResult]:
        url = self.ctx.manifest.source.url
        base = parse_physionet_dataset_base(url)
        if not base:
            yield DownloadFileResult(status="error", message="unable to parse PhysioNet dataset URL", url=url)
            return
        db, version = base
        prefix = f"{db}/{version}/"

        try:
            import boto3
            from botocore import UNSIGNED
            from botocore.config import Config as BotoConfig
        except Exception as exc:
            yield DownloadFileResult(status="error", message=f"boto3/botocore required for PhysioNet: {exc}", url=url)
            return

        client = boto3.client("s3", config=BotoConfig(signature_version=UNSIGNED))
        paginator = client.get_paginator("list_objects_v2")

        with tempfile.TemporaryDirectory(prefix="ecg-pipeline-physionet-") as tmp:
            tmp_dir = Path(tmp)
            object_count = 0
            for page in paginator.paginate(Bucket=self._physionet_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj.get("Key", "")
                    if not key or key.endswith("/"):
                        continue

                    object_count += 1
                    rel = key[len(prefix) :] if key.startswith(prefix) else Path(key).name
                    local_path = tmp_dir / rel
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        client.download_file(self._physionet_bucket, key, str(local_path))
                    except Exception as exc:
                        yield DownloadFileResult(status="error", file=rel, url=url, message=str(exc), s3_key=key)
                        continue

                    dest_key = f"{self.ctx.raw_prefix}{rel}"
                    uploaded = self.ctx.s3_client.upload_file(bucket=self.ctx.bucket, key=dest_key, filename=local_path)
                    yield DownloadFileResult(
                        status="ok",
                        file=rel,
                        url=url,
                        message="downloaded",
                        s3_key=uploaded.key,
                        size_bytes=int(obj.get("Size", 0) or 0),
                    )

            if object_count == 0:
                yield DownloadFileResult(
                    status="error",
                    message=f"no S3 objects found for s3://{self._physionet_bucket}/{prefix}",
                    url=url,
                )

