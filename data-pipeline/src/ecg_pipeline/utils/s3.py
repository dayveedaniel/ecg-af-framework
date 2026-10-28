from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class S3Location:
    bucket: str
    key: str

    def s3_uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


class S3Client:
    """
    Thin wrapper around boto3 S3 client.

    Designed for MinIO (path-style addressing) and AWS S3.
    """

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
        region_name: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.region_name = region_name or "us-east-1"
        config = BotoConfig(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 10, "mode": "standard"},
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=self.region_name,
            config=config,
        )

    @property
    def raw(self):
        return self._client

    def ensure_bucket(self, bucket: str) -> None:
        try:
            self._client.head_bucket(Bucket=bucket)
        except ClientError:
            self._client.create_bucket(Bucket=bucket)

    def prefix_exists(self, bucket: str, prefix: str) -> bool:
        resp = self._client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        return bool(resp.get("Contents"))

    def iter_keys(self, bucket: str, prefix: str) -> Iterable[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key")
                if key:
                    yield key

    def delete_key(self, *, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def delete_prefix(self, *, bucket: str, prefix: str) -> int:
        deleted = 0
        batch: list[dict[str, str]] = []
        for key in self.iter_keys(bucket, prefix):
            batch.append({"Key": key})
            if len(batch) == 1000:
                self._client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
                deleted += len(batch)
                batch = []
        if batch:
            self._client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch)
        return deleted

    def put_bytes(
        self,
        *,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> S3Location:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            extra["Metadata"] = metadata
        self._client.put_object(Bucket=bucket, Key=key, Body=data, **extra)
        return S3Location(bucket=bucket, key=key)

    def put_json(
        self,
        *,
        bucket: str,
        key: str,
        obj: Any,
        metadata: dict[str, str] | None = None,
    ) -> S3Location:
        data = json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")
        return self.put_bytes(bucket=bucket, key=key, data=data, content_type="application/json", metadata=metadata)

    def upload_file(self, *, bucket: str, key: str, filename: str | Path) -> S3Location:
        filename = Path(filename)
        self._client.upload_file(str(filename), bucket, key)
        return S3Location(bucket=bucket, key=key)

    def download_file(self, *, bucket: str, key: str, filename: str | Path) -> Path:
        filename = Path(filename)
        filename.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(bucket, key, str(filename))
        return filename

    def get_bytes(self, *, bucket: str, key: str) -> bytes:
        resp = self._client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read()
        return body
