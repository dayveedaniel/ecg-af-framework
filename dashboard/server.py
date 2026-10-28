from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

from ecg_benchmark.config import DEFAULT_ARTIFACT_BUCKET, DEFAULT_ARTIFACT_PREFIX, DEFAULT_CACHE_PREFIX
from ecg_benchmark.storage import S3ArtifactStore

from dashboard.assets import read_static_asset, read_template
from dashboard.results import _parse_int
from dashboard.runner import DockerComposeCommandFactory, ExperimentRunner
from dashboard.services import DashboardDataService
from dashboard.settings import (
    DEFAULT_DOCKER_COMPOSE_FILES,
    DEFAULT_DOCKER_CONTAINER_JOB_DIR,
    DEFAULT_DOCKER_ENV_FILE,
    DEFAULT_DOCKER_JOB_DIR,
    DEFAULT_DOCKER_SERVICE,
    DEFAULT_JOB_DIR,
    DEFAULT_LOG_DIRS,
)


def serve_dashboard(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    artifact_bucket: str = DEFAULT_ARTIFACT_BUCKET,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
    cache_prefix: str = DEFAULT_CACHE_PREFIX,
    s3_config_prefix: str = "benchmarks/configs",
    log_dirs: Iterable[str | Path] | None = None,
    runner_backend: str = "local",
    runner_job_dir: str | Path | None = None,
    docker_compose_files: Iterable[str | Path] | None = None,
    docker_env_file: str | Path | None = DEFAULT_DOCKER_ENV_FILE,
    docker_service: str = DEFAULT_DOCKER_SERVICE,
    docker_container_job_dir: str = DEFAULT_DOCKER_CONTAINER_JOB_DIR,
) -> None:
    """Serve the benchmark dashboard until interrupted."""

    store = S3ArtifactStore.from_env()
    runner_backend = runner_backend.strip().lower()
    command_factory = None
    job_dir = Path(runner_job_dir) if runner_job_dir else DEFAULT_JOB_DIR
    if runner_backend == "docker-compose":
        job_dir = Path(runner_job_dir) if runner_job_dir else DEFAULT_DOCKER_JOB_DIR
        command_factory = DockerComposeCommandFactory(
            compose_files=docker_compose_files or DEFAULT_DOCKER_COMPOSE_FILES,
            env_file=docker_env_file,
            service=docker_service,
            host_job_root=job_dir,
            container_job_root=docker_container_job_dir,
        )
    elif runner_backend != "local":
        raise ValueError("runner_backend must be 'local' or 'docker-compose'")

    service_log_dirs = list(log_dirs) if log_dirs is not None else list(DEFAULT_LOG_DIRS)
    service_log_dirs.append(job_dir)
    service = DashboardDataService(
        store=store,
        artifact_bucket=artifact_bucket,
        artifact_prefix=artifact_prefix,
        cache_prefix=cache_prefix,
        s3_config_prefix=s3_config_prefix,
        log_dirs=service_log_dirs,
    )
    runner = ExperimentRunner(
        store=store,
        artifact_bucket=artifact_bucket,
        s3_config_prefix=s3_config_prefix,
        job_dir=job_dir,
        command_factory=command_factory,
    )
    handler = make_dashboard_handler(service, runner=runner)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"ECG benchmark dashboard: http://{host}:{port}")
    print(f"Artifact root: s3://{artifact_bucket}/{artifact_prefix.strip('/')}/")
    print(f"Cache root: s3://{artifact_bucket}/{cache_prefix.strip('/')}/")
    print(f"Config source prefix: s3://{artifact_bucket}/{s3_config_prefix.strip('/')}/")
    print(f"Runner backend: {runner_backend}")
    print(f"Runner job dir: {Path(job_dir).resolve()}")
    if runner_backend == "docker-compose":
        print(f"Runner Docker service: {docker_service}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped")
    finally:
        server.server_close()


def make_dashboard_handler(
    service: DashboardDataService,
    *,
    runner: ExperimentRunner | None = None,
) -> type[BaseHTTPRequestHandler]:
    class DashboardRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib method name.
            parsed = urlparse(self.path)
            try:
                if parsed.path in {"/", "/index.html"}:
                    self._send_html(read_template("index.html"))
                    return
                if parsed.path.startswith("/static/"):
                    name = unquote(parsed.path.removeprefix("/static/")).strip("/")
                    try:
                        data, content_type = read_static_asset(name)
                    except FileNotFoundError:
                        self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                        return
                    self._send_bytes(data, content_type=content_type)
                    return
                if parsed.path == "/api/health":
                    self._send_json(service.health())
                    return
                if parsed.path == "/api/runs":
                    params = parse_qs(parsed.query)
                    limit = _parse_int(params.get("limit", ["50"])[0], default=50)
                    self._send_json(service.list_runs(limit=limit))
                    return
                if parsed.path == "/api/config/options":
                    self._send_json(service.config_options())
                    return
                if parsed.path == "/api/cache":
                    params = parse_qs(parsed.query)
                    dataset = (params.get("dataset") or [None])[0]
                    self._send_json(service.list_cache(dataset=dataset))
                    return
                if parsed.path.rstrip("/") == "/api/explorer":
                    self._send_json(service.explorer())
                    return
                if parsed.path == "/api/runner/options":
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(runner.options())
                    return
                if parsed.path == "/api/jobs":
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(runner.list_jobs())
                    return
                if parsed.path.startswith("/api/jobs/"):
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    job_id = unquote(parsed.path.removeprefix("/api/jobs/")).strip("/")
                    if not job_id:
                        self._send_json({"error": "Missing job id"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    try:
                        self._send_json(runner.job_snapshot(job_id))
                    except KeyError:
                        self._send_json({"error": "Job not found"}, status=HTTPStatus.NOT_FOUND)
                    return
                if parsed.path.startswith("/api/runs/"):
                    run_id = unquote(parsed.path.removeprefix("/api/runs/")).strip("/")
                    if not run_id:
                        self._send_json({"error": "Missing run id"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    self._send_json(service.get_run(run_id))
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            except Exception as exc:  # pragma: no cover - defensive request boundary.
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name.
            parsed = urlparse(self.path)
            try:
                payload = self._read_json_body()
                if parsed.path == "/api/config/validate":
                    self._send_json(service.validate_config_payload(payload))
                    return
                if parsed.path == "/api/config/save":
                    response, status = service.save_config_payload(payload)
                    self._send_json(response, status=status)
                    return
                if parsed.path == "/api/config/upload":
                    response, status = service.upload_config_payload(payload)
                    self._send_json(response, status=status)
                    return
                if parsed.path == "/api/runner/config":
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    response, status = runner.load_config_source(payload)
                    self._send_json(response, status=status)
                    return
                if parsed.path == "/api/jobs":
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    response, status = runner.submit_job(payload)
                    self._send_json(response, status=status)
                    return
                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    job_id = unquote(parsed.path.removeprefix("/api/jobs/").removesuffix("/cancel")).strip("/")
                    response, status = runner.cancel_job(job_id)
                    self._send_json(response, status=status)
                    return
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover - defensive request boundary.
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib method name.
            parsed = urlparse(self.path)
            try:
                payload = self._read_json_body()
                if parsed.path == "/api/cache":
                    response, status = service.delete_cache_entry(payload)
                    self._send_json(response, status=status)
                    return
                if parsed.path.startswith("/api/runs/"):
                    run_id = unquote(parsed.path.removeprefix("/api/runs/")).strip("/")
                    if not run_id:
                        self._send_json({"error": "Missing run id"}, status=HTTPStatus.BAD_REQUEST)
                        return
                    response, status = service.delete_run(run_id)
                    self._send_json(response, status=status)
                    return
                if parsed.path.startswith("/api/jobs/"):
                    if runner is None:
                        self._send_json({"error": "Runner unavailable"}, status=HTTPStatus.NOT_FOUND)
                        return
                    job_id = unquote(parsed.path.removeprefix("/api/jobs/")).strip("/")
                    response, status = runner.delete_job(job_id)
                    self._send_json(response, status=status)
                    return
                self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # pragma: no cover - defensive request boundary.
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib API.
            print(f"{self.address_string()} - {format % args}")

        def _read_json_body(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(content_length) if content_length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON body: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_html(self, body: str) -> None:
            self._send_bytes(body.encode("utf-8"), content_type="text/html; charset=utf-8")

        def _send_bytes(self, data: bytes, *, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return DashboardRequestHandler
