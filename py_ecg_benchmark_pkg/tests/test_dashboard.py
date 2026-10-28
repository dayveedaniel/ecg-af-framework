from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ecg_benchmark.config import load_config
from ecg_benchmark.dashboard import (
    ArtifactObject,
    DashboardDataService,
    ExperimentRunner,
    attach_timing_rows,
    make_dashboard_handler,
    parse_run_log,
    pivot_metric_rows,
)
from dashboard.results import label_distribution_rows, parse_job_file


class FakeStore:
    def __init__(self, objects: dict[str, Any]) -> None:
        self.objects = objects

    def get_bytes(self, bucket: str, key: str) -> bytes:
        del bucket
        if key not in self.objects:
            raise KeyError(key)
        value = self.objects[key]
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")

    def iter_objects(self, bucket: str, prefix: str):
        del bucket
        modified = dt.datetime(2026, 5, 14, 9, 5, tzinfo=dt.timezone.utc)
        for key, value in sorted(self.objects.items()):
            if key.startswith(prefix):
                yield ArtifactObject(key=key, last_modified=modified, size=len(str(value)))

    def iter_keys(self, bucket: str, prefix: str):
        del bucket
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key

    def prefix_exists(self, bucket: str, prefix: str) -> bool:
        del bucket
        return any(key.startswith(prefix) for key in self.objects)

    def put_text(self, bucket: str, key: str, text: str, *, content_type=None) -> str:
        del bucket, content_type
        self.objects[key] = text
        return f"s3://ecg-pipeline/{key}"

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        del bucket
        keys = [key for key in self.objects if key.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)


def test_parse_run_log_extracts_run_time(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(
        "\n".join(
            [
                "started 2026-05-14T08:31:51+00:00",
                "run_id showcase-smoke",
                "finished 2026-05-14T09:05:46+00:00 exit_code=0",
            ]
        ),
        encoding="utf-8",
    )

    info = parse_run_log(log)

    assert info["run_id"] == "showcase-smoke"
    assert info["status"] == "succeeded"
    assert info["elapsed_seconds"] == 2035.0
    assert info["elapsed_label"] == "33m 55s"


def test_dashboard_service_summarizes_artifact_metrics(tmp_path: Path) -> None:
    run_id = "run-1"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "run-1.log").write_text(
        "started 2026-05-14T08:00:00+00:00\n"
        "run_id run-1\n"
        "finished 2026-05-14T08:02:05+00:00 exit_code=0\n",
        encoding="utf-8",
    )
    config = {
        "random_seed": 7,
        "experiment": {"name": "showcase-smoke"},
        "data": {
            "source_datasets": ["aftdb"],
            "target_datasets": ["mitdb"],
            "sample_rate_hz": 250,
            "window_seconds": 10,
            "stride_seconds": 5,
            "recording_aggregation": "mean",
            "recording_label_policy": "fraction_threshold",
        },
        "models": [{"id": "dummy_prior"}, {"id": "rr_irregularity_rule"}],
        "metrics": ["n", "auroc", "f1", "accuracy"],
    }
    window_metrics = {
        "run_id": run_id,
        "evaluation_level": "window",
        "metrics": [
            metric_row(run_id, "aftdb", "mitdb", "dummy_prior", "window", "auroc", 0.51),
            metric_row(run_id, "aftdb", "mitdb", "dummy_prior", "window", "f1", 0.2),
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "window", "auroc", 0.91),
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "window", "f1", 0.77),
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "window", "n", 100.0),
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "window", "positive", 25.0),
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "window", "negative", 75.0),
        ],
    }
    recording_metrics = {
        "run_id": run_id,
        "evaluation_level": "recording",
        "metrics": [
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "recording", "auroc", 0.83),
            metric_row(run_id, "aftdb", "mitdb", "rr_irregularity_rule", "recording", "f1", 0.7),
        ],
    }
    timings = {
        "run_id": run_id,
        "timings": [
            timing_row(run_id, "train", "aftdb", None, "rr_irregularity_rule", 12.0, row_count=50),
            timing_row(run_id, "evaluate", "aftdb", "mitdb", "rr_irregularity_rule", 3.0, row_count=100),
            timing_row(run_id, "inference", "aftdb", "mitdb", "rr_irregularity_rule", 0.4, row_count=100),
        ],
    }
    objects = {
        f"benchmarks/runs/{run_id}/config/resolved_config.json": json.dumps(config),
        f"benchmarks/runs/{run_id}/metrics/window_metrics.json": json.dumps(window_metrics),
        f"benchmarks/runs/{run_id}/metrics/recording_metrics.json": json.dumps(recording_metrics),
        f"benchmarks/runs/{run_id}/timings/timings.json": json.dumps(timings),
        f"benchmarks/runs/{run_id}/matrix/window_benchmark_matrix.csv": (
            "source_dataset,target_dataset,model_id,auroc\n"
            "aftdb,mitdb,rr_irregularity_rule,0.91\n"
        ),
    }
    service = DashboardDataService(store=FakeStore(objects), log_dirs=[log_dir])

    listed = service.list_runs()
    detail = service.get_run(run_id)

    assert listed["runs"][0]["run_id"] == run_id
    assert listed["runs"][0]["elapsed_label"] == "2m 5s"
    assert listed["runs"][0]["best"]["window"]["model_id"] == "rr_irregularity_rule"
    assert listed["runs"][0]["best"]["window"]["metric_value"] == 0.91
    assert detail["run"]["experiment_name"] == "showcase-smoke"
    assert detail["comparisons"]["window"][1]["auroc"] == 0.91
    assert detail["comparisons"]["window"][1]["train_evaluation_seconds"] == 15.0
    assert detail["comparisons"]["window"][1]["inference_seconds"] == 0.4
    assert detail["distributions"]["window"][0]["n"] == 100.0
    assert len(detail["timings"]) == 3
    assert detail["matrices"]["window"][0]["model_id"] == "rr_irregularity_rule"


def test_dashboard_service_uses_dashboard_job_status(tmp_path: Path) -> None:
    run_id = "run-failed"
    job_dir = tmp_path / "dashboard_jobs" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": "job-1",
                "status": "failed",
                "config_name": "failed.yaml",
                "config_source": "package",
                "config_path": str(job_dir / "failed.yaml"),
                "progress_path": str(job_dir / "progress.jsonl"),
                "stdout_path": str(job_dir / "stdout.log"),
                "stderr_path": str(job_dir / "stderr.log"),
                "job_path": str(job_dir / "job.json"),
                "submitted_at": "2026-06-04T08:00:00Z",
                "started_at": "2026-06-04T08:01:00Z",
                "finished_at": "2026-06-04T08:02:00Z",
                "run_id": run_id,
                "exit_code": 1,
            }
        ),
        encoding="utf-8",
    )
    objects = {
        f"benchmarks/runs/{run_id}/config/resolved_config.json": json.dumps({"experiment": {"name": "failed"}})
    }
    service = DashboardDataService(store=FakeStore(objects), log_dirs=[tmp_path])

    listed = service.list_runs()
    detail = service.get_run(run_id)

    assert listed["runs"][0]["status"] == "failed"
    assert detail["run"]["status"] == "failed"
    assert detail["run"]["elapsed_label"] == "1m 0s"


def test_dashboard_service_lists_and_deletes_cache_entries() -> None:
    cache_key = "a" * 64
    objects = {
        f"benchmarks/cache/features/aftdb/{cache_key}/feature_manifest.json": json.dumps(
            {
                "dataset_name": "aftdb",
                "cache_key": cache_key,
                "sample_rate_hz": 250,
                "window_seconds": 10,
                "stride_seconds": 5,
                "source_recordings": 2,
                "labeled_windows": 12,
            }
        ),
        f"benchmarks/cache/features/aftdb/{cache_key}/windows.parquet/part-00000.parquet": "window-data",
        f"benchmarks/cache/features/aftdb/{cache_key}/waveform_windows.parquet/part-00000.parquet": "wave-data",
    }
    store = FakeStore(objects)
    service = DashboardDataService(store=store, log_dirs=[])

    cache = service.list_cache()

    assert cache["datasets"] == ["aftdb"]
    assert len(cache["entries"]) == 1
    assert cache["entries"][0]["kind"] == "features"
    assert cache["entries"][0]["dataset"] == "aftdb"
    assert cache["entries"][0]["window_seconds"] == 10

    response, status = service.delete_cache_entry(
        {"kind": "features", "dataset": "aftdb", "cache_key": cache_key}
    )

    assert status.value == 200
    assert response["deleted_objects"] == 3
    assert not store.objects


def test_dashboard_service_deletes_run_artifacts_and_local_records(tmp_path: Path) -> None:
    run_id = "run-delete"
    job_dir = tmp_path / "dashboard_jobs" / "job-delete"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": "job-delete",
                "status": "succeeded",
                "config_name": "delete.yaml",
                "config_source": "package",
                "config_path": str(job_dir / "delete.yaml"),
                "progress_path": str(job_dir / "progress.jsonl"),
                "stdout_path": str(job_dir / "stdout.log"),
                "stderr_path": str(job_dir / "stderr.log"),
                "job_path": str(job_dir / "job.json"),
                "submitted_at": "2026-06-04T08:00:00Z",
                "run_id": run_id,
            }
        ),
        encoding="utf-8",
    )
    store = FakeStore(
        {
            f"benchmarks/runs/{run_id}/config/resolved_config.json": "{}",
            f"benchmarks/runs/{run_id}/metrics/window_metrics.json": "{}",
        }
    )
    service = DashboardDataService(store=store, log_dirs=[tmp_path])

    response, status = service.delete_run(run_id)

    assert status.value == 200
    assert response["deleted_objects"] == 2
    assert not store.objects
    assert not job_dir.exists()


def test_pivot_metric_rows_groups_tidy_metric_rows() -> None:
    rows = [
        metric_row("run", "src", "tgt", "model", "window", "auroc", "0.75"),
        metric_row("run", "src", "tgt", "model", "window", "f1", "0.55"),
    ]

    assert pivot_metric_rows(rows) == [
        {
            "evaluation_level": "window",
            "source_dataset": "src",
            "target_dataset": "tgt",
            "model_id": "model",
            "auroc": 0.75,
            "f1": 0.55,
        }
    ]


def test_attach_timing_rows_adds_train_eval_and_inference_seconds() -> None:
    rows = [
        {
            "evaluation_level": "window",
            "source_dataset": "src",
            "target_dataset": "tgt",
            "model_id": "model",
            "auroc": 0.75,
        }
    ]
    timings = [
        timing_row("run", "train", "src", None, "model", 0.0, row_count=20, requires_training=False, wall_duration_seconds=0.1),
        timing_row("run", "evaluate", "src", "tgt", "model", 2.5, row_count=5),
        timing_row("run", "inference", "src", "tgt", "model", 0.25, row_count=5),
    ]

    enriched = attach_timing_rows(rows, timings)

    assert enriched[0]["train_seconds"] == 0.0
    assert enriched[0]["requires_training"] is False
    assert enriched[0]["training_wall_seconds"] == 0.1
    assert enriched[0]["evaluation_seconds"] == 2.5
    assert enriched[0]["train_evaluation_seconds"] == 2.5
    assert enriched[0]["inference_seconds"] == 0.25
    assert enriched[0]["input_kind"] == "tabular"


def test_label_distribution_rows_derives_counts() -> None:
    rows = label_distribution_rows(
        [
            {
                "evaluation_level": "window",
                "source_dataset": "src",
                "target_dataset": "tgt",
                "model_id": "model",
                "n": 10,
                "positive": 3,
                "negative": 7,
            }
        ]
    )

    assert rows[0]["positive_fraction"] == 0.3


def test_label_distribution_rows_deduplicates_target_cohorts() -> None:
    rows = label_distribution_rows(
        [
            {
                "evaluation_level": "window",
                "source_dataset": "src-a",
                "target_dataset": "tgt",
                "model_id": "model-a",
                "input_kind": "waveform",
                "n": 10,
                "positive": 3,
                "negative": 7,
            },
            {
                "evaluation_level": "window",
                "source_dataset": "src-b",
                "target_dataset": "tgt",
                "model_id": "model-b",
                "input_kind": "waveform",
                "n": 10,
                "positive": 3,
                "negative": 7,
            },
            {
                "evaluation_level": "window",
                "source_dataset": "src-a",
                "target_dataset": "tgt",
                "model_id": "model-c",
                "input_kind": "waveform",
                "n": 5,
                "positive": 1,
                "negative": 4,
            },
        ]
    )

    assert len(rows) == 2
    assert rows[0]["n"] == 5.0
    assert rows[1]["n"] == 10.0
    assert rows[1]["source_datasets"] == ["src-a", "src-b"]
    assert rows[1]["model_ids"] == ["model-a", "model-b"]


def test_parse_job_file_extracts_failed_status(tmp_path: Path) -> None:
    path = tmp_path / "job.json"
    path.write_text(
        json.dumps(
            {
                "job_id": "job-1",
                "status": "failed",
                "run_id": "run-1",
                "started_at": "2026-06-04T08:00:00Z",
                "finished_at": "2026-06-04T08:00:05Z",
                "exit_code": 1,
            }
        ),
        encoding="utf-8",
    )

    info = parse_job_file(path)

    assert info["run_id"] == "run-1"
    assert info["status"] == "failed"
    assert info["elapsed_seconds"] == 5.0


def test_config_options_include_presets_and_model_reference(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )

    options = service.config_options()

    assert {preset["id"] for preset in options["presets"]} >= {"custom", "baseline", "showcase_smoke"}
    assert "auroc" in options["metrics"]
    assert "af_or_afl_vs_non_af" in options["enums"]["label_policies"]
    assert "dummy_prior" in {model["model_id"] for model in options["models"]}
    assert options["defaults"]["data"]["source_datasets"] == []
    assert options["defaults"]["data"]["label_policy"] == "af_vs_nsr"


def test_explorer_returns_model_metadata_without_catalog(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )

    payload = service.explorer()

    assert "dummy_prior" in {model["model_id"] for model in payload["models"]}
    assert payload["datasets"] == []
    assert payload["warnings"]


def test_validate_config_payload_returns_resolved_yaml_and_hash(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )

    result = service.validate_config_payload({"config": valid_config()})

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["config_hash"]
    assert result["artifact_root_preview"] == "s3://ecg-pipeline/benchmarks/runs/<run_id>/"
    assert "source_datasets:" in result["yaml"]


def test_validate_config_payload_reports_errors(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )
    config = valid_config()
    config["data"]["source_datasets"] = []

    result = service.validate_config_payload({"config": config})

    assert result["ok"] is False
    assert "data.source_datasets must contain at least one dataset name" in result["errors"]


def test_save_config_payload_writes_safe_yaml_and_prevents_overwrite(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )

    result, status = service.save_config_payload({"config": valid_config()})
    path = Path(result["path"])
    second, second_status = service.save_config_payload({"config": valid_config()})

    assert status == 200
    assert path.exists()
    assert load_config(path).config_hash() == result["config_hash"]
    assert "validate-config" in result["command_examples"]["validate"]
    assert second_status == 409
    assert second["ok"] is False


def test_upload_config_payload_writes_s3_yaml_and_prevents_overwrite(tmp_path: Path) -> None:
    store = FakeStore({})
    service = DashboardDataService(
        store=store,
        log_dirs=[],
        generated_config_dir=tmp_path,
        s3_config_prefix="benchmarks/configs",
    )

    result, status = service.upload_config_payload({"config": valid_config(), "filename": "api-test.yaml"})
    second, second_status = service.upload_config_payload({"config": valid_config(), "filename": "api-test.yaml"})

    assert status == 200
    assert result["key"] == "benchmarks/configs/api-test.yaml"
    assert result["s3_uri"] == "s3://ecg-pipeline/benchmarks/configs/api-test.yaml"
    assert "source_datasets:" in store.objects["benchmarks/configs/api-test.yaml"]
    assert second_status == 409
    assert second["ok"] is False


def test_save_config_payload_rejects_unsafe_filename(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )

    result, status = service.save_config_payload(
        {
            "config": valid_config(),
            "filename": "../escape.yaml",
        }
    )

    assert status == 400
    assert result["ok"] is False


def test_config_api_endpoints_with_fake_service(tmp_path: Path) -> None:
    service = DashboardDataService(
        store=FakeStore({}),
        log_dirs=[],
        generated_config_dir=tmp_path,
    )
    runner = ExperimentRunner(store=FakeStore({}), job_dir=tmp_path / "jobs", auto_start=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_dashboard_handler(service, runner=runner))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        options = get_json(f"{root}/api/config/options")
        cache = get_json(f"{root}/api/cache")
        runner_options = get_json(f"{root}/api/runner/options")
        explorer = get_json(f"{root}/api/explorer")
        jobs = get_json(f"{root}/api/jobs")
        validation = post_json(f"{root}/api/config/validate", {"config": valid_config()})
        saved = post_json(
            f"{root}/api/config/save",
            {"config": valid_config(), "filename": "api-test.yaml"},
        )
        uploaded = post_json(
            f"{root}/api/config/upload",
            {"config": valid_config(), "filename": "api-test-upload.yaml"},
        )
        conflict = post_json(
            f"{root}/api/config/save",
            {"config": valid_config(), "filename": "api-test.yaml"},
            expected_status=409,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "baseline" in {preset["id"] for preset in options["presets"]}
    assert cache["entries"] == []
    assert "package_examples" in runner_options
    assert "models" in explorer
    assert jobs["jobs"] == []
    assert validation["ok"] is True
    assert saved["ok"] is True
    assert Path(saved["path"]).exists()
    assert "run" in saved["command_examples"]
    assert uploaded["ok"] is True
    assert uploaded["key"] == "benchmarks/configs/api-test-upload.yaml"
    assert conflict["ok"] is False


def get_json(url: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], *, expected_status: int = 200) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            assert response.status == expected_status
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        assert exc.code == expected_status
        return json.loads(exc.read().decode("utf-8"))


def valid_config() -> dict[str, Any]:
    return {
        "random_seed": 7,
        "experiment": {"name": "dashboard-test", "description": "Created in tests."},
        "data": {
            "source_datasets": ["dataset-a"],
            "target_datasets": ["dataset-a"],
            "sample_rate_hz": 250,
            "processed_prefix": "processed",
            "window_seconds": 10,
            "stride_seconds": 5,
            "label_policy": "af_vs_nsr",
            "lead_policy": "single_preferred_lead",
            "preferred_leads": ["II", "MLII"],
            "recording_aggregation": "mean",
            "recording_label_policy": "fraction_threshold",
            "recording_label_threshold": 0.5,
            "split": {
                "train": 0.7,
                "val": 0.15,
                "test": 0.15,
                "stratify_by": "dataset_af_present",
            },
        },
        "models": [{"id": "dummy_prior"}],
        "metrics": ["n", "auroc", "accuracy"],
    }


def metric_row(
    run_id: str,
    source_dataset: str,
    target_dataset: str,
    model_id: str,
    evaluation_level: str,
    metric_name: str,
    metric_value: float | str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source_dataset": source_dataset,
        "target_dataset": target_dataset,
        "model_id": model_id,
        "evaluation_level": evaluation_level,
        "metric_name": metric_name,
        "metric_value": metric_value,
    }


def timing_row(
    run_id: str,
    stage: str,
    source_dataset: str,
    target_dataset: str | None,
    model_id: str,
    duration_seconds: float,
    *,
    row_count: int,
    requires_training: bool | None = None,
    wall_duration_seconds: float | None = None,
) -> dict[str, Any]:
    row = {
        "run_id": run_id,
        "stage": stage,
        "source_dataset": source_dataset,
        "target_dataset": target_dataset,
        "model_id": model_id,
        "input_kind": "tabular",
        "duration_seconds": duration_seconds,
        "row_count": row_count,
    }
    if requires_training is not None:
        row["requires_training"] = requires_training
    if wall_duration_seconds is not None:
        row["wall_duration_seconds"] = wall_duration_seconds
    return row
