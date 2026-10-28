from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from ecg_benchmark.dashboard import DockerComposeCommandFactory, ExperimentRunner
from ecg_benchmark.runtime import detect_java_home


class FakeStore:
    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = objects

    def get_bytes(self, bucket: str, key: str) -> bytes:
        del bucket
        return self.objects[key].encode("utf-8")

    def iter_keys(self, bucket: str, prefix: str):
        del bucket
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key


def test_runner_loads_s3_config_and_validates_it(tmp_path: Path) -> None:
    store = FakeStore({"benchmarks/configs/smoke.yaml": valid_yaml()})
    runner = ExperimentRunner(store=store, job_dir=tmp_path, auto_start=False)

    options = runner.options()
    payload, status = runner.load_config_source(
        {"source": "s3", "key": "benchmarks/configs/smoke.yaml"}
    )

    assert status.value == 200
    assert options["s3_configs"][0]["key"] == "benchmarks/configs/smoke.yaml"
    assert payload["ok"] is True
    assert payload["filename"] == "smoke.yaml"


def test_runner_queues_and_removes_uploaded_config(tmp_path: Path) -> None:
    runner = ExperimentRunner(store=FakeStore({}), job_dir=tmp_path, auto_start=False)

    payload, status = runner.submit_job(
        {"source": "upload", "filename": "queued.yaml", "yaml": valid_yaml()}
    )
    job_id = payload["job"]["job_id"]
    removed, removed_status = runner.remove_job(job_id)

    assert status.value == 200
    assert payload["job"]["status"] == "queued"
    assert removed_status.value == 200
    assert removed["job"]["status"] == "removed"
    assert (tmp_path / job_id / "job.json").exists()


def test_runner_deletes_non_running_job_files(tmp_path: Path) -> None:
    runner = ExperimentRunner(store=FakeStore({}), job_dir=tmp_path, auto_start=False)

    payload, _ = runner.submit_job(
        {"source": "upload", "filename": "queued.yaml", "yaml": valid_yaml()}
    )
    job_id = payload["job"]["job_id"]
    deleted, status = runner.delete_job(job_id)

    assert status.value == 200
    assert deleted["job"]["job_id"] == job_id
    assert not (tmp_path / job_id).exists()
    assert runner.list_jobs()["jobs"] == []


def test_runner_progress_snapshot_tracks_stages(tmp_path: Path) -> None:
    runner = ExperimentRunner(store=FakeStore({}), job_dir=tmp_path, auto_start=False)
    payload, _ = runner.submit_job(
        {"source": "upload", "filename": "progress.yaml", "yaml": valid_yaml(), "run_id": "run-1"}
    )
    job_id = payload["job"]["job_id"]
    progress_path = tmp_path / job_id / "progress.jsonl"
    events = [
        {"event": "plan", "total": 1, "timestamp": "2026-06-04T00:00:00Z"},
        {
            "event": "stage_started",
            "stage_id": "0001-train",
            "stage": "train",
            "label": "Train model",
            "timestamp": "2026-06-04T00:00:01Z",
        },
        {
            "event": "stage_finished",
            "stage_id": "0001-train",
            "status": "succeeded",
            "timestamp": "2026-06-04T00:00:02Z",
        },
        {
            "event": "run_resolved",
            "run_id": "run-1",
            "artifact_root": "s3://ecg-pipeline/benchmarks/runs/run-1/",
            "timestamp": "2026-06-04T00:00:03Z",
        },
    ]
    progress_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    snapshot = runner.job_snapshot(job_id)

    assert snapshot["progress"]["completed"] == 1
    assert snapshot["progress"]["percent"] == 100.0
    assert snapshot["progress"]["run_id"] == "run-1"
    assert snapshot["progress"]["stages"][0]["status"] == "succeeded"


def test_detect_java_home_from_java_on_path(tmp_path: Path, monkeypatch) -> None:
    java_home = tmp_path / "jdk"
    java_bin = java_home / "bin"
    java_bin.mkdir(parents=True)
    java_executable = java_bin / "java"
    java_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    java_executable.chmod(0o755)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setenv("PATH", str(java_bin))

    assert detect_java_home() == str(java_home)


def test_runner_subprocess_env_uses_current_python(tmp_path: Path) -> None:
    runner = ExperimentRunner(store=FakeStore({}), job_dir=tmp_path, auto_start=False)

    env = runner._subprocess_env()

    assert env["PYSPARK_PYTHON"]
    assert env["PYSPARK_DRIVER_PYTHON"]


def test_docker_compose_command_factory_maps_job_paths(tmp_path: Path) -> None:
    job_root = tmp_path / "dashboard_jobs"
    job_dir = job_root / "job-1"
    job_dir.mkdir(parents=True)
    factory = DockerComposeCommandFactory(
        compose_files=[tmp_path / "docker-compose.yaml", tmp_path / "docker-compose.spark.yaml"],
        env_file=tmp_path / ".env.docker",
        service="spark-eda",
        host_job_root=job_root,
        container_job_root="/home/jovyan/work/dashboard_jobs",
    )
    job = SimpleNamespace(
        config_path=str(job_dir / "config.yaml"),
        progress_path=str(job_dir / "progress.jsonl"),
        run_id="run-1",
    )

    command = factory(job)

    assert command[:2] == ["docker", "compose"]
    assert command[2:4] == ["--env-file", str((tmp_path / ".env.docker").resolve())]
    assert command[-7:] == [
        "run",
        "--config",
        "/home/jovyan/work/dashboard_jobs/job-1/config.yaml",
        "--progress-file",
        "/home/jovyan/work/dashboard_jobs/job-1/progress.jsonl",
        "--run-id",
        "run-1",
    ]
    assert "spark-eda" in command


def valid_yaml() -> str:
    return """
random_seed: 7
data:
  source_datasets:
    - dataset-a
models:
  - id: dummy_prior
metrics:
  - auroc
"""
