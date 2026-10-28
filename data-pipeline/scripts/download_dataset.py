#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from ecg_pipeline.config import default_pipeline_root, load_app_config
from ecg_pipeline.downloader.downloader import Downloader
from ecg_pipeline.manifest import load_manifest
from ecg_pipeline.utils.s3 import S3Client


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a dataset (Module 1) to S3/MinIO")
    parser.add_argument("--manifest", required=True, help="Path to dataset manifest YAML")
    parser.add_argument("--pipeline-root", default=str(default_pipeline_root()), help="Path to data-pipeline/")
    parser.add_argument("--env-file", default=".env", help="Env file name under pipeline-root (e.g. .env.docker)")
    parser.add_argument("--force", action="store_true", help="Override manifest and force re-download")
    args = parser.parse_args()

    app = load_app_config(args.pipeline_root, env_filename=args.env_file)
    manifest = load_manifest(args.manifest)

    s3 = S3Client(
        endpoint_url=app.env.s3_endpoint_url,
        access_key=app.env.s3_access_key,
        secret_key=app.env.s3_secret_key,
    )
    try:
        s3.ensure_bucket(app.pipeline.s3_bucket)
    except Exception as exc:
        print(f"Failed to access/create bucket {app.pipeline.s3_bucket}: {exc}", file=sys.stderr)
        return 2

    downloader = Downloader(manifest=manifest, s3_client=s3, config=app.pipeline)
    force_redownload = manifest.force_redownload or args.force
    result = downloader.run(force_redownload=force_redownload)

    print("Download complete")
    print(f"  dataset: {result.dataset_name}")
    print(f"  s3_prefix: s3://{app.pipeline.s3_bucket}/{result.s3_prefix}")
    print(f"  files_ok: {result.file_count}")
    print(f"  total_size_bytes: {result.total_size_bytes}")
    print(f"  skipped: {result.skipped}")
    print(f"  force_redownload: {force_redownload}")
    if result.report_key:
        print(f"  report: s3://{app.pipeline.s3_bucket}/{result.report_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
