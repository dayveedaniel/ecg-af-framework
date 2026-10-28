from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.decorators import task, task_group

from dag_reports import load_ok_results_from_processing_report
from dag_shared import (
    as_bool,
    build_s3,
    load_cfg,
    load_dataset_manifest,
    manifests_dir,
    resolve_manifest_paths,
    run_params_from_context,
)
from ecg_pipeline.downloader.downloader import Downloader
from ecg_pipeline.reader.processor import process_dataset
from ecg_pipeline.registrar import MetadataRegistrar


cfg = load_cfg()

default_args = {
    "retries": cfg.airflow_task_retries,
    "retry_delay": timedelta(seconds=cfg.airflow_retry_delay_seconds),
    "retry_exponential_backoff": cfg.airflow_retry_exponential_backoff,
}

with DAG(
    dag_id="ecg_dataset_pipeline",
    description="End-to-end dataset pipeline per dataset: download -> read/parse/resample -> register",
    start_date=datetime(2025, 1, 1),
    schedule=None,
    catchup=False,
    default_args=default_args,
    tags=["ecg", "pipeline"],
    params={
        "all_manifests": True,
        "dataset_name": None,
        "force_redownload": False,
        "target_rate": None,
    },
) as dag:
    @task(task_id="load_manifest")
    def load_manifest_task() -> list[str]:
        conf, params = run_params_from_context()
        dataset_name = conf.get("dataset_name") or params.get("dataset_name")
        all_manifests = as_bool(
            conf.get("all_manifests", params.get("all_manifests", False)),
            key="all_manifests",
        )
        manifest_paths = resolve_manifest_paths(
            manifests_dir=manifests_dir(),
            all_manifests=all_manifests,
            dataset_name=str(dataset_name) if dataset_name else None,
        )
        return [path.stem for path in manifest_paths]

    @task(task_id="download_extract_prune")
    def download_extract_prune_task(dataset_name: str) -> dict[str, Any]:
        conf, params = run_params_from_context()
        force_redownload = as_bool(
            conf.get("force_redownload", params.get("force_redownload", False)),
            key="force_redownload",
        )

        manifest = load_dataset_manifest(dataset_name)
        cfg = load_cfg()
        s3 = build_s3()
        s3.ensure_bucket(cfg.s3_bucket)

        download_result = Downloader(manifest=manifest, s3_client=s3, config=cfg).run(
            force_redownload=force_redownload,
        )
        return {
            "dataset_name": dataset_name,
            "download_ok_files": int(download_result.file_count),
        }

    @task(task_id="process_dataset")
    def process_dataset_task(download_meta: dict[str, Any]) -> dict[str, Any]:
        dataset_name = str(download_meta["dataset_name"])

        conf, params = run_params_from_context()
        target_rate = conf.get("target_rate", params.get("target_rate"))
        if target_rate is not None:
            target_rate = int(target_rate)

        manifest = load_dataset_manifest(dataset_name)
        cfg = load_cfg()
        s3 = build_s3()
        s3.ensure_bucket(cfg.s3_bucket)

        ok, failed, report = process_dataset(
            manifest=manifest,
            s3_client=s3,
            config=cfg,
            target_rate=target_rate,
            spark_master=os.environ.get("SPARK_MASTER"),
        )
        return {
            "dataset_name": dataset_name,
            "download_ok_files": int(download_meta.get("download_ok_files", 0)),
            "processed_ok_recordings": len(ok),
            "processed_error_recordings": len(failed),
            "processing_report": report,
        }

    @task(task_id="register_results")
    def register_results_task(process_meta: dict[str, Any]) -> dict[str, Any]:
        dataset_name = str(process_meta["dataset_name"])
        report_uri = str(process_meta["processing_report"])

        manifest = load_dataset_manifest(dataset_name)
        ok_results = load_ok_results_from_processing_report(report_uri=report_uri)

        import os
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            dbname=os.environ.get("POSTGRES_DB"),
            user=os.environ.get("POSTGRES_USER"),
            password=os.environ.get("POSTGRES_PASSWORD"),
        )
        try:
            registrar = MetadataRegistrar(conn)
            dataset_id = registrar.register_dataset(manifest)
            registered = registrar.register_recordings(dataset_name, ok_results)
        finally:
            conn.close()

        return {
            "dataset_name": dataset_name,
            "dataset_id": dataset_id,
            "download_ok_files": int(process_meta.get("download_ok_files", 0)),
            "processed_ok_recordings": int(process_meta.get("processed_ok_recordings", len(ok_results))),
            "processed_error_recordings": int(process_meta.get("processed_error_recordings", 0)),
            "registered_recordings": registered,
            "processing_report": report_uri,
        }

    @task_group(group_id="dataset_pipeline")
    def dataset_pipeline_group(dataset_name: str) -> None:
        download_meta = download_extract_prune_task(dataset_name=dataset_name)
        process_meta = process_dataset_task(download_meta=download_meta)
        register_results_task(process_meta=process_meta)

    dataset_names = load_manifest_task()
    dataset_pipeline_group.expand(dataset_name=dataset_names)
