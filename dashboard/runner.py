from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from ecg_benchmark.config import DEFAULT_ARTIFACT_BUCKET
from ecg_benchmark.runtime import ensure_java_home

from dashboard.config_builder import config_to_yaml, validate_builder_config
from dashboard.settings import DEFAULT_JOB_DIR, EXAMPLES_DIR


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "removed"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".json"}


@dataclass
class ExperimentJob:
    job_id: str
    status: str
    config_name: str
    config_source: str
    config_path: str
    progress_path: str
    stdout_path: str
    stderr_path: str
    job_path: str
    submitted_at: str
    run_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    removed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DockerComposeCommandFactory:
    """Build `docker compose exec` commands for dashboard-launched runs."""

    def __init__(
        self,
        *,
        compose_files: Iterable[str | Path],
        service: str,
        host_job_root: str | Path,
        container_job_root: str,
        env_file: str | Path | None = None,
        python_executable: str = "python",
    ) -> None:
        self.compose_files = [Path(path).resolve() for path in compose_files]
        self.env_file = Path(env_file).resolve() if env_file else None
        self.service = service
        self.host_job_root = Path(host_job_root).resolve()
        self.container_job_root = PurePosixPath(container_job_root)
        self.python_executable = python_executable

    def __call__(self, job: ExperimentJob) -> list[str]:
        command = ["docker", "compose"]
        if self.env_file is not None:
            command.extend(["--env-file", str(self.env_file)])
        for compose_file in self.compose_files:
            command.extend(["-f", str(compose_file)])
        command.extend(
            [
                "exec",
                "-T",
                self.service,
                self.python_executable,
                "-m",
                "ecg_benchmark.cli",
                "run",
                "--config",
                self._container_path(job.config_path),
                "--progress-file",
                self._container_path(job.progress_path),
            ]
        )
        if job.run_id:
            command.extend(["--run-id", job.run_id])
        return command

    def _container_path(self, path: str | Path) -> str:
        host_path = Path(path).resolve()
        try:
            relative = host_path.relative_to(self.host_job_root)
        except ValueError as exc:
            raise ValueError(f"job path {host_path} is outside host job root {self.host_job_root}") from exc
        return str(self.container_job_root.joinpath(*relative.parts))


class ExperimentRunner:
    """One-at-a-time dashboard experiment queue backed by subprocesses."""

    def __init__(
        self,
        *,
        store: Any,
        artifact_bucket: str = DEFAULT_ARTIFACT_BUCKET,
        s3_config_prefix: str = "benchmarks/configs",
        job_dir: str | Path = DEFAULT_JOB_DIR,
        auto_start: bool = True,
        command_factory: Callable[[ExperimentJob], list[str]] | None = None,
    ) -> None:
        self.store = store
        self.artifact_bucket = artifact_bucket
        self.s3_config_prefix = s3_config_prefix.strip("/")
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self._command_factory = command_factory or self._default_command
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._jobs: dict[str, ExperimentJob] = {}
        self._order: list[str] = []
        self._processes: dict[str, subprocess.Popen] = {}
        self._stop = False
        self._load_jobs()
        self._worker: threading.Thread | None = None
        if auto_start:
            self._worker = threading.Thread(target=self._worker_loop, name="dashboard-experiment-runner", daemon=True)
            self._worker.start()

    def options(self) -> dict[str, Any]:
        return {
            "package_examples": self.package_examples(),
            "s3_configs": self.s3_configs(),
            "s3_config_prefix": self.s3_config_prefix,
            "job_dir": str(self.job_dir),
        }

    def package_examples(self) -> list[dict[str, str]]:
        rows = []
        for path in _package_example_paths():
            rows.append({"name": path.name, "label": path.name})
        return rows

    def s3_configs(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        try:
            for key in self._iter_s3_keys(self.s3_config_prefix):
                suffix = Path(key).suffix.lower()
                if suffix in CONFIG_EXTENSIONS:
                    rows.append({"key": key, "label": key.removeprefix(f"{self.s3_config_prefix}/")})
        except Exception:
            return []
        return sorted(rows, key=lambda item: item["key"])

    def load_config_source(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        source = str(payload.get("source") or "").strip()
        try:
            filename, text = self._config_text_from_source(source, payload)
        except ValueError as exc:
            return {"ok": False, "errors": [str(exc)]}, HTTPStatus.BAD_REQUEST
        validation = self._validate_yaml_text(text)
        return {
            **validation,
            "filename": filename,
            "source": source,
            "yaml": validation.get("yaml") or text,
        }, HTTPStatus.OK if validation.get("ok") else HTTPStatus.BAD_REQUEST

    def submit_job(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        config_text = str(payload.get("yaml") or "")
        filename = _safe_filename(str(payload.get("filename") or "benchmark-config.yaml"))
        if not config_text.strip():
            return {"ok": False, "errors": ["yaml is required"]}, HTTPStatus.BAD_REQUEST
        validation = self._validate_yaml_text(config_text)
        if not validation.get("ok"):
            return validation, HTTPStatus.BAD_REQUEST
        job_id = _new_job_id()
        job_root = self.job_dir / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        config_path = job_root / filename
        config_path.write_text(str(validation.get("yaml") or config_text), encoding="utf-8")
        job = ExperimentJob(
            job_id=job_id,
            status="queued",
            config_name=filename,
            config_source=str(payload.get("source") or "upload"),
            config_path=str(config_path),
            progress_path=str(job_root / "progress.jsonl"),
            stdout_path=str(job_root / "stdout.log"),
            stderr_path=str(job_root / "stderr.log"),
            job_path=str(job_root / "job.json"),
            submitted_at=_utc_now(),
            run_id=_optional_run_id(payload.get("run_id")),
            metadata={
                "config_hash": validation.get("config_hash"),
                "artifact_root_preview": validation.get("artifact_root_preview"),
            },
        )
        self._save_job(job)
        with self._condition:
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._condition.notify_all()
        return {"ok": True, "job": self.job_snapshot(job_id)}, HTTPStatus.OK

    def list_jobs(self) -> dict[str, Any]:
        with self._lock:
            jobs = [self.job_snapshot(job_id) for job_id in self._order if job_id in self._jobs]
        return {
            "jobs": jobs,
            "active": next((job for job in jobs if job["status"] == "running"), None),
            "queued": [job for job in jobs if job["status"] == "queued"],
            "history": [job for job in jobs if job["status"] in TERMINAL_STATUSES],
        }

    def job_snapshot(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs[job_id]
            payload = asdict(job)
        payload["progress"] = self._progress_snapshot(Path(job.progress_path))
        payload["stdout_tail"] = _tail_text(Path(job.stdout_path))
        payload["stderr_tail"] = _tail_text(Path(job.stderr_path))
        return payload

    def remove_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return {"ok": False, "errors": ["job not found"]}, HTTPStatus.NOT_FOUND
            if job.status != "queued":
                return {"ok": False, "errors": ["only queued jobs can be removed"]}, HTTPStatus.CONFLICT
            job.status = "removed"
            job.removed_at = _utc_now()
            job.finished_at = job.removed_at
            self._save_job(job)
            self._condition.notify_all()
        return {"ok": True, "job": self.job_snapshot(job_id)}, HTTPStatus.OK

    def delete_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return {"ok": False, "errors": ["job not found"]}, HTTPStatus.NOT_FOUND
            if job.status == "running":
                return {"ok": False, "errors": ["cancel the running job before deleting it"]}, HTTPStatus.CONFLICT
            payload = asdict(job)
            self._jobs.pop(job_id, None)
            self._order = [item for item in self._order if item != job_id]
            self._condition.notify_all()
        job_root = Path(payload["job_path"]).parent
        try:
            shutil.rmtree(job_root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return {"ok": False, "errors": [f"Could not delete job files: {exc}"]}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {"ok": True, "job": payload, "deleted_path": str(job_root)}, HTTPStatus.OK

    def cancel_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return {"ok": False, "errors": ["job not found"]}, HTTPStatus.NOT_FOUND
            if job.status == "queued":
                job.status = "removed"
                job.removed_at = _utc_now()
                job.finished_at = job.removed_at
                self._save_job(job)
                self._condition.notify_all()
                return {"ok": True, "job": self.job_snapshot(job_id)}, HTTPStatus.OK
            if job.status != "running":
                return {"ok": False, "errors": ["only running or queued jobs can be cancelled"]}, HTTPStatus.CONFLICT
            process = self._processes.get(job_id)
            if process is not None and process.poll() is None:
                process.terminate()
        return {"ok": True, "job": self.job_snapshot(job_id)}, HTTPStatus.OK

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._stop:
                    job = self._next_queued_job()
                    if job is not None:
                        break
                    self._condition.wait(timeout=1.0)
                if self._stop:
                    return
                job.status = "running"
                job.started_at = _utc_now()
                self._save_job(job)
            self._run_job(job)

    def _next_queued_job(self) -> ExperimentJob | None:
        for job_id in self._order:
            job = self._jobs.get(job_id)
            if job is not None and job.status == "queued":
                return job
        return None

    def _run_job(self, job: ExperimentJob) -> None:
        command = self._command_factory(job)
        env = self._subprocess_env()
        with Path(job.stdout_path).open("ab") as stdout, Path(job.stderr_path).open("ab") as stderr:
            try:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
                with self._condition:
                    self._processes[job.job_id] = process
                exit_code = process.wait()
            except Exception as exc:
                exit_code = -1
                with self._condition:
                    job.error = str(exc)
            finally:
                with self._condition:
                    self._processes.pop(job.job_id, None)

        progress = self._progress_snapshot(Path(job.progress_path))
        with self._condition:
            job.exit_code = exit_code
            job.finished_at = _utc_now()
            if progress.get("run_id"):
                job.run_id = str(progress["run_id"])
            if job.status == "running":
                if exit_code in {-15, -9}:
                    job.status = "cancelled"
                else:
                    job.status = "succeeded" if exit_code == 0 else "failed"
            if exit_code not in {0, -15, -9} and not job.error:
                job.error = f"ecg-bench exited with code {exit_code}"
            self._save_job(job)
            self._condition.notify_all()

    def _default_command(self, job: ExperimentJob) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "ecg_benchmark.cli",
            "run",
            "--config",
            job.config_path,
            "--progress-file",
            job.progress_path,
        ]
        if job.run_id:
            command.extend(["--run-id", job.run_id])
        return command

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        ensure_java_home(env)
        env.setdefault("PYSPARK_PYTHON", sys.executable)
        env.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
        return env

    def _config_text_from_source(self, source: str, payload: dict[str, Any]) -> tuple[str, str]:
        if source == "package":
            name = Path(str(payload.get("name") or "")).name
            if not name:
                raise ValueError("package example name is required")
            path = _package_example_path(name)
            if path is None:
                raise ValueError(f"Unknown package example: {name}")
            return path.name, path.read_text(encoding="utf-8")
        if source == "s3":
            key = str(payload.get("key") or "").strip().lstrip("/")
            if not key:
                raise ValueError("S3 config key is required")
            if Path(key).suffix.lower() not in CONFIG_EXTENSIONS:
                raise ValueError("S3 config key must end with .yaml, .yml, or .json")
            text = self.store.get_bytes(self.artifact_bucket, key).decode("utf-8")
            return Path(key).name, text
        if source == "upload":
            text = str(payload.get("yaml") or "")
            filename = _safe_filename(str(payload.get("filename") or "uploaded-config.yaml"))
            if not text.strip():
                raise ValueError("uploaded YAML is empty")
            return filename, text
        raise ValueError("source must be package, s3, or upload")

    def _validate_yaml_text(self, text: str) -> dict[str, Any]:
        try:
            import yaml
        except ImportError as exc:
            return {"ok": False, "errors": [f"PyYAML is required to validate configs: {exc}"]}
        try:
            raw = yaml.safe_load(text) or {}
        except Exception as exc:
            return {"ok": False, "errors": [f"YAML parse error: {exc}"]}
        if not isinstance(raw, dict):
            return {"ok": False, "errors": ["Config file must contain a top-level mapping"]}
        validation = validate_builder_config(raw, check_connections=False, store=self.store)
        if validation.get("ok"):
            validation["yaml"] = config_to_yaml(validation["resolved_config"])
        return validation

    def _progress_snapshot(self, path: Path) -> dict[str, Any]:
        events = _read_jsonl(path)
        stages: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        total = None
        run_id = None
        artifact_root = None
        for event in events:
            name = event.get("event")
            if name == "plan":
                total = event.get("total")
            elif name == "run_resolved":
                run_id = event.get("run_id")
                artifact_root = event.get("artifact_root")
            elif name == "stage_started":
                stage = {
                    "stage_id": event.get("stage_id"),
                    "stage": event.get("stage"),
                    "label": event.get("label"),
                    "status": "running",
                    "started_at": event.get("timestamp"),
                    "finished_at": None,
                    "metadata": {key: value for key, value in event.items() if key not in {"event", "stage_id", "stage", "label", "timestamp"}},
                }
                stages.append(stage)
                if stage["stage_id"]:
                    by_id[str(stage["stage_id"])] = stage
            elif name == "stage_finished":
                stage = by_id.get(str(event.get("stage_id")))
                if stage is not None:
                    stage["status"] = event.get("status") or "succeeded"
                    stage["finished_at"] = event.get("timestamp")
                    stage["result"] = {key: value for key, value in event.items() if key not in {"event", "stage_id", "status", "timestamp"}}
        completed = sum(1 for stage in stages if stage["status"] != "running")
        total_value = int(total or max(len(stages), completed, 0))
        percent = (completed / total_value * 100.0) if total_value else 0.0
        return {
            "events": events[-200:],
            "stages": stages,
            "total": total_value,
            "completed": completed,
            "percent": round(min(100.0, percent), 2),
            "run_id": run_id,
            "artifact_root": artifact_root,
        }

    def _iter_s3_keys(self, prefix: str):
        if hasattr(self.store, "iter_keys"):
            yield from self.store.iter_keys(self.artifact_bucket, prefix.rstrip("/"))
            return
        raw = getattr(self.store, "raw", None)
        if raw is not None:
            paginator = raw.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.artifact_bucket, Prefix=prefix.rstrip("/")):
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    if key:
                        yield str(key)

    def _load_jobs(self) -> None:
        for path in sorted(self.job_dir.glob("*/job.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = ExperimentJob(**payload)
            except Exception:
                continue
            if job.status == "running":
                job.status = "failed"
                job.finished_at = _utc_now()
                job.error = "Dashboard restarted while this job was running"
                self._save_job(job)
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)

    def _save_job(self, job: ExperimentJob) -> None:
        path = Path(job.job_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(job), indent=2, sort_keys=True), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _package_example_paths() -> list[Path]:
    candidates = [
        EXAMPLES_DIR,
        Path.cwd() / "py_ecg_benchmark_pkg" / "examples",
        Path(__file__).resolve().parents[1] / "py_ecg_benchmark_pkg" / "examples",
    ]
    seen: set[Path] = set()
    paths: list[Path] = []
    for directory in candidates:
        directory = directory.resolve()
        if directory in seen or not directory.exists():
            continue
        seen.add(directory)
        paths.extend(path for path in directory.iterdir() if path.suffix.lower() in CONFIG_EXTENSIONS)
    return sorted({path.resolve() for path in paths}, key=lambda item: item.name)


def _package_example_path(name: str) -> Path | None:
    for path in _package_example_paths():
        if path.name == name:
            return path
    return None


def _tail_text(path: Path, *, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _new_job_id() -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:10]}"


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    if not name:
        name = "benchmark-config.yaml"
    if Path(name).suffix.lower() not in CONFIG_EXTENSIONS:
        name = f"{name}.yaml"
    cleaned = "".join(char if char.isalnum() or char in "_.=-" else "-" for char in name)
    return cleaned.strip("-._") or "benchmark-config.yaml"


def _optional_run_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
