#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from ecg_pipeline.config import default_pipeline_root, load_app_config
from ecg_pipeline.manifest import load_manifest
from ecg_pipeline.reader import process_dataset
from ecg_pipeline.utils.s3 import S3Client


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a dataset from raw/ to processed/ (Module 2)")
    parser.add_argument("--manifest", required=True, help="Path to dataset manifest YAML")
    parser.add_argument("--pipeline-root", default=str(default_pipeline_root()), help="Path to data-pipeline/")
    parser.add_argument("--env-file", default=".env", help="Env file name under pipeline-root")
    parser.add_argument("--target-rate", type=int, default=None, help="Override output sampling rate (Hz)")
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

    try:
        ok, failed, report = process_dataset(
            manifest=manifest,
            s3_client=s3,
            config=app.pipeline,
            target_rate=args.target_rate,
            env=app.env,
        )
    except Exception as exc:
        print(f"Processing failed: {exc}", file=sys.stderr)
        return 2

    print("Processing complete")
    print(f"  dataset: {manifest.dataset.name}")
    print(f"  ok_recordings: {len(ok)}")
    print(f"  error_recordings: {len(failed)}")
    print(f"  report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
