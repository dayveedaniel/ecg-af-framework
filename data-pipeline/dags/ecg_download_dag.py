from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.decorators import task

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


cfg = load_cfg()

default_args = {
    "retries": cfg.airflow_task_retries,
    "retry_delay": timedelta(seconds=cfg.airflow_retry_delay_seconds),
    "retry_exponential_backoff": cfg.airflow_retry_exponential_backoff,
}


with DAG(
    dag_id="ecg_download_dataset",
    description="Download & extract raw dataset into MinIO/S3 (raw/{dataset_name}/)",
    start_date=datetime(2025, 1, 1),
    schedule=None,
    catchup=False,
    default_args=default_args,
    tags=["ecg", "download"],
    params={
        "all_manifests": True,
        "dataset_name": None,
        "force_redownload": False,
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
    def download_extract_prune_task(dataset_name: str) -> dict[str, int | str]:
        conf, params = run_params_from_context()
        force_redownload = as_bool(
            conf.get("force_redownload", params.get("force_redownload", False)),
            key="force_redownload",
        )

        manifest = load_dataset_manifest(dataset_name)
        cfg = load_cfg()
        s3 = build_s3()
        s3.ensure_bucket(cfg.s3_bucket)

        result = Downloader(manifest=manifest, s3_client=s3, config=cfg).run(
            force_redownload=force_redownload,
        )
        return {
            "dataset_name": dataset_name,
            "download_ok_files": int(result.file_count),
        }

    dataset_names = load_manifest_task()
    download_extract_prune_task.expand(dataset_name=dataset_names)
