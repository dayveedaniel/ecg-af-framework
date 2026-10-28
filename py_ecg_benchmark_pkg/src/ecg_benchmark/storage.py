from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


class S3ArtifactStore:
    """Small boto3 wrapper for MinIO/AWS S3 benchmark artifacts."""

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        access_key: str | None,
        secret_key: str | None,
        region_name: str | None = None,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config as BotoConfig
        except ImportError as exc:
            raise RuntimeError("boto3 and botocore are required for S3 artifact storage") from exc

        config = BotoConfig(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 10, "mode": "standard"},
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name or "us-east-1",
            config=config,
        )

    @classmethod
    def from_env(cls, storage: Any | None = None) -> "S3ArtifactStore":
        endpoint_env = str(getattr(storage, "endpoint_env", "S3_ENDPOINT_URL"))
        access_key_env = str(getattr(storage, "access_key_env", "S3_ACCESS_KEY"))
        secret_key_env = str(getattr(storage, "secret_key_env", "S3_SECRET_KEY"))
        return cls(
            endpoint_url=os.environ.get(endpoint_env),
            access_key=os.environ.get(access_key_env),
            secret_key=os.environ.get(secret_key_env),
        )

    @property
    def raw(self) -> Any:
        return self._client

    def ensure_bucket(self, bucket: str) -> None:
        try:
            self._client.head_bucket(Bucket=bucket)
        except Exception:
            self._client.create_bucket(Bucket=bucket)

    def prefix_exists(self, bucket: str, prefix: str) -> bool:
        response = self._client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        return bool(response.get("Contents"))

    def iter_keys(self, bucket: str, prefix: str) -> Iterable[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj.get("Key")
                if key:
                    yield key

    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            extra["Metadata"] = metadata
        self._client.put_object(Bucket=bucket, Key=key, Body=data, **extra)
        return f"s3://{bucket}/{key}"

    def put_json(
        self,
        bucket: str,
        key: str,
        payload: Any,
        *,
        metadata: dict[str, str] | None = None,
    ) -> str:
        data = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return self.put_bytes(bucket, key, data, content_type="application/json", metadata=metadata)

    def put_text(
        self,
        bucket: str,
        key: str,
        text: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> str:
        return self.put_bytes(bucket, key, text.encode("utf-8"), content_type=content_type)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        response = self._client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def upload_file(self, bucket: str, key: str, filename: str | Path) -> str:
        self._client.upload_file(str(filename), bucket, key)
        return f"s3://{bucket}/{key}"

    def download_file(self, bucket: str, key: str, filename: str | Path) -> Path:
        filename = Path(filename)
        filename.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(bucket, key, str(filename))
        return filename

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        keys = list(self.iter_keys(bucket, prefix))
        deleted = 0
        for cursor in range(0, len(keys), 1000):
            chunk = keys[cursor : cursor + 1000]
            if not chunk:
                continue
            response = self._client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
            )
            deleted += len(chunk) - len(response.get("Errors", []))
        return deleted
