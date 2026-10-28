#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from ecg_pipeline.config import default_pipeline_root, load_app_config
from ecg_pipeline.downloader.downloader import Downloader
from ecg_pipeline.manifest import load_manifest
from ecg_pipeline.reader import process_dataset
from ecg_pipeline.registrar import MetadataRegistrar
from ecg_pipeline.utils.s3 import S3Client


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dataset pipeline: download -> process -> register")
    parser.add_argument("--manifest", required=True, help="Path to dataset manifest YAML")
    parser.add_argument("--pipeline-root", default=str(default_pipeline_root()), help="Path to data-pipeline/")
    parser.add_argument("--env-file", default=".env", help="Env file name under pipeline-root")
    parser.add_argument("--force-download", action="store_true", help="Force re-download even if raw/ exists")
    parser.add_argument("--target-rate", type=int, default=None, help="Override output sampling rate (Hz)")
    parser.add_argument("--skip-download", action="store_true", help="Skip Module 1 and process existing raw/")
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

    if not args.skip_download:
        try:
            download_result = Downloader(manifest=manifest, s3_client=s3, config=app.pipeline).run(
                force_redownload=args.force_download
            )
            print("Download stage complete")
            print(f"  files_ok: {download_result.file_count}")
            print(f"  skipped: {download_result.skipped}")
        except Exception as exc:
            print(f"Download stage failed: {exc}", file=sys.stderr)
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
        print(f"Process stage failed: {exc}", file=sys.stderr)
        return 2

    try:
        import psycopg2

        conn = psycopg2.connect(
            host=app.env.postgres_host,
            port=app.env.postgres_port,
            dbname=app.env.postgres_db,
            user=app.env.postgres_user,
            password=app.env.postgres_password,
        )
    except Exception as exc:
        print(f"Failed to connect to Postgres: {exc}", file=sys.stderr)
        return 2

    try:
        registrar = MetadataRegistrar(conn)
        dataset_id = registrar.register_dataset(manifest)
        registered = registrar.register_recordings(manifest.dataset.name, ok)
    except Exception as exc:
        conn.close()
        print(f"Register stage failed: {exc}", file=sys.stderr)
        return 2
    conn.close()

    print("Pipeline run complete")
    print(f"  dataset: {manifest.dataset.name}")
    print(f"  dataset_id: {dataset_id}")
    print(f"  processed_ok: {len(ok)}")
    print(f"  processed_errors: {len(failed)}")
    print(f"  registered_recordings: {registered}")
    print(f"  processing_report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
