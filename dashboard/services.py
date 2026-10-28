from __future__ import annotations

import csv
import io
import json
import re
import shutil
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterable

from ecg_benchmark.artifacts import ArtifactLayout, sanitize_artifact_part, sanitize_run_id
from ecg_benchmark.catalog import BenchmarkCatalog
from ecg_benchmark.config import (
    DEFAULT_ARTIFACT_BUCKET,
    DEFAULT_ARTIFACT_PREFIX,
    DEFAULT_CACHE_PREFIX,
    DEFAULT_METRICS,
    DEFAULT_MODELS,
    DEFAULT_PREFERRED_LEADS,
    SUPPORTED_RECORDING_AGGREGATIONS,
    SUPPORTED_RECORDING_LABEL_POLICIES,
    SUPPORTED_REUSE_MODES,
    SUPPORTED_SPLIT_STRATIFICATIONS,
)
from ecg_benchmark.labels import SUPPORTED_LABEL_POLICIES
from ecg_benchmark.reporting import model_reference

from dashboard.config_builder import (
    command_examples,
    config_presets,
    default_builder_config,
    resolve_generated_config_path,
    sanitize_config_filename,
    supported_metrics_ordered,
    validate_builder_config,
    _payload_config,
)
from dashboard.results import (
    _default_log_dirs,
    _first_non_empty,
    _isoformat,
    _latest_datetime,
    _summary_sort_key,
    attach_timing_rows,
    best_metrics,
    elapsed_between,
    format_duration,
    label_distribution_rows,
    parse_job_file,
    parse_run_log,
    pivot_metric_rows,
    status_from_artifacts,
    summarize_artifacts,
    summarize_config,
)
from dashboard.settings import DEFAULT_GENERATED_CONFIG_DIR, EVALUATION_LEVELS, PRESENTED_ARTIFACTS
from dashboard.types import ArtifactObject


class DashboardDataService:
    """Read benchmark run results from the existing S3/MinIO artifact layout."""

    def __init__(
        self,
        *,
        store: Any,
        artifact_bucket: str = DEFAULT_ARTIFACT_BUCKET,
        artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
        cache_prefix: str = DEFAULT_CACHE_PREFIX,
        s3_config_prefix: str = "benchmarks/configs",
        log_dirs: Iterable[str | Path] | None = None,
        generated_config_dir: str | Path = DEFAULT_GENERATED_CONFIG_DIR,
    ) -> None:
        self.store = store
        self.bucket = artifact_bucket
        self.prefix = artifact_prefix.strip("/")
        self.cache_prefix = cache_prefix.strip("/")
        self.s3_config_prefix = s3_config_prefix.strip("/")
        self.log_dirs = _default_log_dirs(log_dirs)
        self.generated_config_dir = Path(generated_config_dir)

    def health(self) -> dict[str, Any]:
        return {
            "artifact_bucket": self.bucket,
            "artifact_prefix": self.prefix,
            "artifact_root": self.artifact_root,
            "cache_prefix": self.cache_prefix,
            "cache_root": self.cache_root,
            "s3_config_prefix": self.s3_config_prefix,
            "s3_config_root": self.s3_config_root,
            "log_dirs": [str(path) for path in self.log_dirs],
        }

    @property
    def artifact_root(self) -> str:
        prefix = f"/{self.prefix}" if self.prefix else ""
        return f"s3://{self.bucket}{prefix}/"

    @property
    def cache_root(self) -> str:
        prefix = f"/{self.cache_prefix}" if self.cache_prefix else ""
        return f"s3://{self.bucket}{prefix}/"

    @property
    def s3_config_root(self) -> str:
        prefix = f"/{self.s3_config_prefix}" if self.s3_config_prefix else ""
        return f"s3://{self.bucket}{prefix}/"

    def list_runs(self, *, limit: int = 50) -> dict[str, Any]:
        object_groups: dict[str, list[ArtifactObject]] = {}
        errors: list[str] = []
        try:
            for obj in self._iter_objects(self._artifact_prefix_for_listing()):
                run_id = self._run_id_from_key(obj.key)
                if run_id:
                    object_groups.setdefault(run_id, []).append(obj)
        except Exception as exc:  # pragma: no cover - exercised manually against live S3/MinIO.
            errors.append(f"Could not list artifact objects: {exc}")

        log_index = self._log_index()
        run_ids = set(object_groups) | set(log_index)
        summaries = [
            self._run_summary(run_id, object_groups.get(run_id, []), log_index.get(run_id))
            for run_id in run_ids
        ]
        summaries.sort(key=_summary_sort_key, reverse=True)
        if limit > 0:
            summaries = summaries[:limit]
        return {
            **self.health(),
            "runs": summaries,
            "errors": errors,
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        objects = list(self._iter_objects(self._run_prefix(run_id)))
        log_info = self._log_index().get(run_id)
        config = self._read_json(run_id, "config", "resolved_config.json")
        metrics = {level: self._read_metrics(run_id, level) for level in EVALUATION_LEVELS}
        timings = self._read_timings(run_id)
        comparisons = {level: attach_timing_rows(pivot_metric_rows(rows), timings) for level, rows in metrics.items()}
        distributions = {level: label_distribution_rows(rows) for level, rows in comparisons.items()}
        matrices = {level: self._read_csv(run_id, "matrix", f"{level}_benchmark_matrix.csv") for level in EVALUATION_LEVELS}
        summary = self._run_summary(run_id, objects, log_info, config=config, metrics=metrics)
        return {
            "run": summary,
            "config": config,
            "config_summary": summarize_config(config),
            "metrics": metrics,
            "timings": timings,
            "comparisons": comparisons,
            "distributions": distributions,
            "matrices": matrices,
            "artifacts": self._artifact_catalog(run_id, objects),
            "run_summary_markdown": self._read_text(run_id, "RUN_SUMMARY.md"),
        }

    def list_cache(self, *, dataset: str | None = None) -> dict[str, Any]:
        object_groups: dict[tuple[str, str, str], list[ArtifactObject]] = {}
        errors: list[str] = []
        try:
            for obj in self._iter_objects(self._cache_prefix_for_listing()):
                parsed = self._cache_group_from_key(obj.key)
                if parsed is None:
                    continue
                object_groups.setdefault(parsed, []).append(obj)
        except Exception as exc:  # pragma: no cover - exercised manually against live S3/MinIO.
            errors.append(f"Could not list cache objects: {exc}")

        entries = [
            self._cache_entry(kind, dataset_part, cache_key, objects)
            for (kind, dataset_part, cache_key), objects in object_groups.items()
        ]
        entries.sort(key=lambda item: (item["kind"], item["dataset"], item["cache_key"]))
        datasets = sorted({entry["dataset"] for entry in entries if entry.get("dataset")})
        if dataset and dataset != "all":
            entries = [entry for entry in entries if entry.get("dataset") == dataset]
        return {
            **self.health(),
            "datasets": datasets,
            "entries": entries,
            "errors": errors,
        }

    def delete_cache_entry(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        kind = str(payload.get("kind") or "").strip()
        dataset = str(payload.get("dataset") or "").strip()
        cache_key = str(payload.get("cache_key") or "").strip()
        if kind not in {"splits", "features"}:
            return {"ok": False, "errors": ["kind must be 'splits' or 'features'"]}, HTTPStatus.BAD_REQUEST
        if not dataset:
            return {"ok": False, "errors": ["dataset is required"]}, HTTPStatus.BAD_REQUEST
        if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
            return {"ok": False, "errors": ["cache_key must be a 64-character lowercase SHA-256 hash"]}, HTTPStatus.BAD_REQUEST
        prefix = f"{self._cache_entry_prefix(kind, sanitize_artifact_part(dataset), cache_key)}/"
        deleted = self.store.delete_prefix(self.bucket, prefix)
        return {
            "ok": True,
            "kind": kind,
            "dataset": dataset,
            "cache_key": cache_key,
            "deleted_objects": deleted,
            "prefix": f"s3://{self.bucket}/{prefix}",
        }, HTTPStatus.OK

    def delete_run(self, run_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        run_id = str(run_id or "").strip()
        if not run_id:
            return {"ok": False, "errors": ["run_id is required"]}, HTTPStatus.BAD_REQUEST
        if sanitize_run_id(run_id) != run_id:
            return {"ok": False, "errors": ["run_id contains unsupported characters"]}, HTTPStatus.BAD_REQUEST
        prefix = f"{self._run_key(run_id)}/"
        deleted_objects = self.store.delete_prefix(self.bucket, prefix)
        deleted_local = self._delete_local_run_records(run_id)
        return {
            "ok": True,
            "run_id": run_id,
            "deleted_objects": deleted_objects,
            "deleted_local_records": deleted_local,
            "prefix": f"s3://{self.bucket}/{prefix}",
        }, HTTPStatus.OK

    def explorer(self) -> dict[str, Any]:
        datasets, warnings = self._dataset_explorer_rows()
        models = model_reference()
        input_kinds = sorted({str(model.get("input_kind") or "unknown") for model in models})
        return {
            **self.health(),
            "datasets": datasets,
            "models": models,
            "input_kinds": input_kinds,
            "warnings": warnings,
        }

    def config_options(self) -> dict[str, Any]:
        datasets, warnings = self._dataset_options()
        return {
            "defaults": default_builder_config(),
            "enums": {
                "label_policies": sorted(SUPPORTED_LABEL_POLICIES),
                "recording_aggregations": sorted(SUPPORTED_RECORDING_AGGREGATIONS),
                "recording_label_policies": sorted(SUPPORTED_RECORDING_LABEL_POLICIES),
                "split_stratifications": sorted(SUPPORTED_SPLIT_STRATIFICATIONS),
                "reuse_modes": sorted(SUPPORTED_REUSE_MODES),
            },
            "metrics": supported_metrics_ordered(),
            "default_metrics": list(DEFAULT_METRICS),
            "models": model_reference(),
            "default_models": list(DEFAULT_MODELS),
            "default_preferred_leads": list(DEFAULT_PREFERRED_LEADS),
            "lead_options": [*DEFAULT_PREFERRED_LEADS, "ECG"],
            "s3_config_prefix": self.s3_config_prefix,
            "s3_config_root": self.s3_config_root,
            "presets": config_presets(),
            "datasets": datasets,
            "warnings": warnings,
        }

    def validate_config_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_config = _payload_config(payload)
        check_connections = bool(payload.get("check_connections", False))
        return validate_builder_config(
            raw_config,
            check_connections=check_connections,
            store=self.store,
        )

    def save_config_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        validation = self.validate_config_payload({"config": _payload_config(payload)})
        if not validation["ok"]:
            return validation, HTTPStatus.BAD_REQUEST
        filename = payload.get("filename")
        try:
            path = resolve_generated_config_path(
                self.generated_config_dir,
                validation["resolved_config"],
                filename=filename,
            )
        except ValueError as exc:
            return {"ok": False, "errors": [str(exc)]}, HTTPStatus.BAD_REQUEST
        if path.exists() and not bool(payload.get("overwrite", False)):
            return {
                "ok": False,
                "errors": [f"Config already exists: {path}. Pass overwrite=true to replace it."],
                "path": str(path),
            }, HTTPStatus.CONFLICT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(validation["yaml"]), encoding="utf-8")
        commands = command_examples(path)
        return {
            **validation,
            "path": str(path),
            "command_examples": commands,
        }, HTTPStatus.OK

    def upload_config_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        validation = self.validate_config_payload(
            {
                "config": _payload_config(payload),
                "check_connections": bool(payload.get("check_connections", False)),
            }
        )
        if not validation["ok"]:
            return validation, HTTPStatus.BAD_REQUEST
        try:
            key = self._s3_config_key(validation["resolved_config"], filename=payload.get("filename"))
        except ValueError as exc:
            return {"ok": False, "errors": [str(exc)]}, HTTPStatus.BAD_REQUEST
        if not bool(payload.get("overwrite", False)) and self.store.prefix_exists(self.bucket, key):
            return {
                "ok": False,
                "errors": [f"Config already exists: s3://{self.bucket}/{key}. Pass overwrite=true to replace it."],
                "key": key,
                "s3_uri": f"s3://{self.bucket}/{key}",
            }, HTTPStatus.CONFLICT
        self.store.put_text(
            self.bucket,
            key,
            str(validation["yaml"]),
            content_type="application/x-yaml; charset=utf-8",
        )
        filename = Path(key).name
        return {
            **validation,
            "key": key,
            "s3_uri": f"s3://{self.bucket}/{key}",
            "filename": filename,
        }, HTTPStatus.OK

    def _dataset_options(self) -> tuple[list[str], list[str]]:
        catalog = None
        try:
            catalog = BenchmarkCatalog.from_env()
            return catalog.dataset_names(), []
        except Exception as exc:
            return [], [f"Dataset catalog unavailable; manual dataset entry is enabled. {exc}"]
        finally:
            if catalog is not None:
                catalog.close()

    def _s3_config_key(self, config: dict[str, Any], *, filename: Any = None) -> str:
        name = str(filename or "").strip()
        if not name:
            experiment = config.get("experiment") if isinstance(config.get("experiment"), dict) else {}
            name = sanitize_config_filename(str(experiment.get("name") or "benchmark-config"))
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("Config filename must be a simple file name, not a path")
        if Path(name).name != name:
            raise ValueError("Config filename must not include path segments")
        if not name.endswith((".yaml", ".yml")):
            name = f"{name}.yaml"
        if not re.fullmatch(r"[A-Za-z0-9_.=-]+\.ya?ml", name):
            raise ValueError("Config filename may only contain letters, numbers, dot, underscore, equals, and dash")
        return "/".join(part.strip("/") for part in [self.s3_config_prefix, name] if part.strip("/"))

    def _dataset_explorer_rows(self) -> tuple[list[dict[str, Any]], list[str]]:
        catalog = None
        try:
            catalog = BenchmarkCatalog.from_env()
            names = catalog.dataset_names()
            recordings = catalog.recordings_for_datasets(names)
        except Exception as exc:
            return [], [f"Dataset catalog unavailable. {exc}"]
        finally:
            if catalog is not None:
                catalog.close()

        grouped: dict[str, dict[str, Any]] = {}
        for name in names:
            grouped[name] = {
                "dataset": name,
                "recordings": 0,
                "af_present": 0,
                "af_absent": 0,
                "af_unknown": 0,
                "total_duration_seconds": 0.0,
                "sampling_rates": set(),
                "lead_names": set(),
                "processed_recordings": 0,
                "annotation_recordings": 0,
            }
        for recording in recordings:
            row = grouped.setdefault(
                recording.dataset_name,
                {
                    "dataset": recording.dataset_name,
                    "recordings": 0,
                    "af_present": 0,
                    "af_absent": 0,
                    "af_unknown": 0,
                    "total_duration_seconds": 0.0,
                    "sampling_rates": set(),
                    "lead_names": set(),
                    "processed_recordings": 0,
                    "annotation_recordings": 0,
                },
            )
            row["recordings"] += 1
            if recording.af_present is True:
                row["af_present"] += 1
            elif recording.af_present is False:
                row["af_absent"] += 1
            else:
                row["af_unknown"] += 1
            row["total_duration_seconds"] += max(0.0, float(recording.duration_seconds or 0.0))
            row["sampling_rates"].add(int(recording.sampling_rate))
            row["lead_names"].update(str(item) for item in (recording.channel_names or []) if item)
            if recording.parquet_path:
                row["processed_recordings"] += 1
            if recording.annotations_path:
                row["annotation_recordings"] += 1

        rows: list[dict[str, Any]] = []
        for row in grouped.values():
            total = row["recordings"]
            af_present = row["af_present"]
            rows.append(
                {
                    **{key: value for key, value in row.items() if key not in {"sampling_rates", "lead_names"}},
                    "af_present_fraction": (af_present / total) if total else None,
                    "total_duration_hours": row["total_duration_seconds"] / 3600.0,
                    "sampling_rates": sorted(row["sampling_rates"]),
                    "lead_names": sorted(row["lead_names"]),
                }
            )
        rows.sort(key=lambda item: str(item.get("dataset") or ""))
        return rows, []

    def _run_summary(
        self,
        run_id: str,
        objects: list[ArtifactObject],
        log_info: dict[str, Any] | None,
        *,
        config: dict[str, Any] | None = None,
        metrics: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        config = config if config is not None else self._read_json(run_id, "config", "resolved_config.json")
        metrics = metrics if metrics is not None else {level: self._read_metrics(run_id, level) for level in EVALUATION_LEVELS}
        config_summary = summarize_config(config)
        artifact_stats = summarize_artifacts(objects)
        best = best_metrics(metrics)
        started_at = _first_non_empty(log_info, "started_at") or artifact_stats["first_artifact_at"]
        finished_at = _first_non_empty(log_info, "finished_at") or artifact_stats["last_artifact_at"]
        elapsed_seconds = _first_non_empty(log_info, "elapsed_seconds")
        if elapsed_seconds is None:
            elapsed_seconds = elapsed_between(started_at, finished_at)
        status = _first_non_empty(log_info, "status") or status_from_artifacts(metrics, objects)
        metric_rows = sum(len(rows) for rows in metrics.values())
        artifact_root = f"{self.artifact_root}{run_id}/"
        return {
            "run_id": run_id,
            "artifact_root": artifact_root,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "elapsed_label": format_duration(elapsed_seconds),
            "metric_row_count": metric_rows,
            "best": best,
            **artifact_stats,
            **config_summary,
            "log": log_info,
        }

    def _artifact_catalog(self, run_id: str, objects: list[ArtifactObject]) -> list[dict[str, Any]]:
        object_by_key = {obj.key: obj for obj in objects}
        rows: list[dict[str, Any]] = []
        for label, parts in PRESENTED_ARTIFACTS:
            key = self._run_key(run_id, *parts)
            obj = object_by_key.get(key)
            rows.append(self._artifact_row(label, key, obj))
        rows.extend(self._artifact_group_rows(run_id, objects))
        return rows

    def _artifact_row(
        self,
        label: str,
        key: str,
        obj: ArtifactObject | None,
    ) -> dict[str, Any]:
        row = {
            "label": label,
            "key": key,
            "uri": f"s3://{self.bucket}/{key}",
            "available": obj is not None,
        }
        if obj is not None:
            row.update(
                {
                    "size": obj.size,
                    "last_modified": _isoformat(obj.last_modified),
                }
            )
        return row

    def _artifact_group_rows(self, run_id: str, objects: list[ArtifactObject]) -> list[dict[str, Any]]:
        if not objects:
            return []
        run_prefix = f"{self._run_key(run_id)}/"
        groups = {
            "features": {
                "label": "Feature parquet objects",
                "prefix": self._run_key(run_id, "features"),
                "count": 0,
                "size": 0,
                "last_modified": None,
            },
            "models": {
                "label": "Model artifact objects",
                "prefix": self._run_key(run_id, "models"),
                "count": 0,
                "size": 0,
                "last_modified": None,
            },
            "window_predictions": {
                "label": "Window prediction parquet objects",
                "prefix": self._run_key(run_id, "predictions", "windows"),
                "count": 0,
                "size": 0,
                "last_modified": None,
            },
            "recording_predictions": {
                "label": "Recording prediction parquet objects",
                "prefix": self._run_key(run_id, "predictions", "recordings"),
                "count": 0,
                "size": 0,
                "last_modified": None,
            },
        }
        for obj in objects:
            rel = obj.key.removeprefix(run_prefix)
            group_key = None
            if rel.startswith("features/") and rel.endswith(".parquet"):
                group_key = "features"
            elif rel.startswith("models/"):
                group_key = "models"
            elif rel.startswith("predictions/windows/") and rel.endswith(".parquet"):
                group_key = "window_predictions"
            elif rel.startswith("predictions/recordings/") and rel.endswith(".parquet"):
                group_key = "recording_predictions"
            if group_key is None:
                continue
            group = groups[group_key]
            group["count"] += 1
            group["size"] += max(0, obj.size)
            group["last_modified"] = _latest_datetime(group["last_modified"], obj.last_modified)
        rows = []
        for group in groups.values():
            if group["count"] <= 0:
                continue
            key = str(group["prefix"])
            rows.append(
                {
                    "label": f"{group['label']} ({group['count']})",
                    "key": key,
                    "uri": f"s3://{self.bucket}/{key}/",
                    "size": group["size"],
                    "last_modified": _isoformat(group["last_modified"]),
                    "available": True,
                    "grouped": True,
                }
            )
        return rows

    def _read_metrics(self, run_id: str, evaluation_level: str) -> list[dict[str, Any]]:
        payload = self._read_json(run_id, "metrics", f"{evaluation_level}_metrics.json")
        if payload is None and evaluation_level == "window":
            payload = self._read_json(run_id, "metrics", "metrics.json")
        if not isinstance(payload, dict):
            return []
        rows = payload.get("metrics", [])
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _read_timings(self, run_id: str) -> list[dict[str, Any]]:
        payload = self._read_json(run_id, "timings", "timings.json")
        if not isinstance(payload, dict):
            return []
        rows = payload.get("timings", [])
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _read_csv(self, run_id: str, *parts: str) -> list[dict[str, Any]]:
        text = self._read_text(run_id, *parts)
        if not text:
            return []
        return list(csv.DictReader(io.StringIO(text)))

    def _read_json(self, run_id: str, *parts: str) -> dict[str, Any] | None:
        text = self._read_text(run_id, *parts)
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _read_json_key(self, key: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.store.get_bytes(self.bucket, key).decode("utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _read_text(self, run_id: str, *parts: str) -> str | None:
        key = self._run_key(run_id, *parts)
        try:
            return self.store.get_bytes(self.bucket, key).decode("utf-8")
        except Exception:
            return None

    def _log_index(self) -> dict[str, dict[str, Any]]:
        logs: dict[str, dict[str, Any]] = {}
        for directory in self.log_dirs:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.log")):
                self._index_log_info(logs, parse_run_log(path))
        for path in self._job_metadata_paths():
            self._index_log_info(logs, parse_job_file(path))
        return logs

    def _index_log_info(self, logs: dict[str, dict[str, Any]], info: dict[str, Any]) -> None:
        run_id = info.get("run_id")
        if not run_id:
            return
        existing = logs.get(str(run_id))
        if existing is None or str(info.get("finished_at") or info.get("started_at") or "") > str(
            existing.get("finished_at") or existing.get("started_at") or ""
        ):
            logs[str(run_id)] = info

    def _job_metadata_paths(self) -> list[Path]:
        roots = [*self.log_dirs]
        paths: set[Path] = set()
        for root in roots:
            if not root.exists():
                continue
            if root.name == "dashboard_jobs":
                paths.update(root.glob("*/job.json"))
            else:
                paths.update(root.glob("dashboard_jobs/*/job.json"))
                paths.update(root.glob("*/job.json"))
                job_json = root / "job.json"
                if job_json.exists():
                    paths.add(job_json)
        return sorted(paths)

    def _delete_local_run_records(self, run_id: str) -> list[str]:
        deleted: list[str] = []
        for directory in self.log_dirs:
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.log")):
                info = parse_run_log(path)
                if info.get("run_id") == run_id:
                    try:
                        path.unlink()
                        deleted.append(str(path))
                    except OSError:
                        continue
        for path in self._job_metadata_paths():
            info = parse_job_file(path)
            if info.get("run_id") != run_id:
                continue
            try:
                if path.parent.name == str(info.get("job_id") or "") and path.parent != path.parent.parent:
                    shutil.rmtree(path.parent)
                    deleted.append(str(path.parent))
                else:
                    path.unlink()
                    deleted.append(str(path))
            except OSError:
                continue
        return deleted

    def _iter_objects(self, prefix: str) -> Iterable[ArtifactObject]:
        if hasattr(self.store, "iter_objects"):
            yield from self.store.iter_objects(self.bucket, prefix)
            return
        raw = getattr(self.store, "raw", None)
        if raw is not None:
            paginator = raw.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    if key:
                        yield ArtifactObject(
                            key=str(key),
                            last_modified=item.get("LastModified"),
                            size=int(item.get("Size") or 0),
                        )
            return
        for key in self.store.iter_keys(self.bucket, prefix):
            yield ArtifactObject(key=key)

    def _artifact_prefix_for_listing(self) -> str:
        return f"{self.prefix}/" if self.prefix else ""

    def _cache_prefix_for_listing(self) -> str:
        return f"{self.cache_prefix}/" if self.cache_prefix else ""

    def _run_prefix(self, run_id: str) -> str:
        return self._run_key(run_id)

    def _run_key(self, run_id: str, *parts: str) -> str:
        return ArtifactLayout(bucket=self.bucket, prefix=self.prefix, run_id=run_id).key(*parts)

    def _cache_group_from_key(self, key: str) -> tuple[str, str, str] | None:
        root = self.cache_prefix.rstrip("/")
        relative = key
        if root:
            prefix = f"{root}/"
            if not key.startswith(prefix):
                return None
            relative = key[len(prefix) :]
        parts = relative.split("/")
        if len(parts) < 4:
            return None
        kind, dataset_part, cache_key = parts[:3]
        if kind not in {"splits", "features"}:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", cache_key):
            return None
        return kind, dataset_part, cache_key

    def _cache_entry_prefix(self, kind: str, dataset_part: str, cache_key: str) -> str:
        return "/".join(part.strip("/") for part in [self.cache_prefix, kind, dataset_part, cache_key] if part.strip("/"))

    def _cache_entry(
        self,
        kind: str,
        dataset_part: str,
        cache_key: str,
        objects: list[ArtifactObject],
    ) -> dict[str, Any]:
        entry_prefix = self._cache_entry_prefix(kind, dataset_part, cache_key)
        manifest_name = "feature_manifest.json" if kind == "features" else "split_manifest.json"
        manifest_key = f"{entry_prefix}/{manifest_name}"
        manifest = self._read_json_key(manifest_key) or {}
        dataset_name = str(manifest.get("dataset_name") or dataset_part)
        last_modified = None
        size = 0
        for obj in objects:
            size += max(0, obj.size)
            last_modified = _latest_datetime(last_modified, obj.last_modified)
        settings = self._cache_entry_settings(kind, manifest)
        return {
            "kind": kind,
            "kind_label": "Feature windows" if kind == "features" else "Split partitions",
            "dataset": dataset_name,
            "dataset_part": dataset_part,
            "cache_key": cache_key,
            "short_key": cache_key[:12],
            "uri": f"s3://{self.bucket}/{entry_prefix}/",
            "manifest_uri": f"s3://{self.bucket}/{manifest_key}",
            "object_count": len(objects),
            "size": size,
            "last_modified": _isoformat(last_modified),
            "complete": bool(manifest),
            "settings": settings,
            **settings,
        }

    def _cache_entry_settings(self, kind: str, manifest: dict[str, Any]) -> dict[str, Any]:
        payload = manifest.get("cache_payload") if isinstance(manifest.get("cache_payload"), dict) else {}
        if kind == "features":
            return {
                "sample_rate_hz": manifest.get("sample_rate_hz") or payload.get("sample_rate_hz"),
                "window_seconds": manifest.get("window_seconds") or payload.get("window_seconds"),
                "stride_seconds": manifest.get("stride_seconds") or payload.get("stride_seconds"),
                "label_policy": manifest.get("label_policy") or payload.get("label_policy"),
                "lead_policy": manifest.get("lead_policy") or payload.get("lead_policy"),
                "source_recordings": manifest.get("source_recordings"),
                "labeled_windows": manifest.get("labeled_windows"),
            }
        split_payload = payload.get("split") if isinstance(payload.get("split"), dict) else {}
        return {
            "random_seed": manifest.get("random_seed") or payload.get("random_seed"),
            "train_ratio": split_payload.get("train") or (manifest.get("ratios") or {}).get("train"),
            "val_ratio": split_payload.get("val") or (manifest.get("ratios") or {}).get("val"),
            "test_ratio": split_payload.get("test") or (manifest.get("ratios") or {}).get("test"),
            "stratify_by": split_payload.get("stratify_by"),
            "source_recordings": manifest.get("source_recordings"),
            "counts": manifest.get("counts") or {},
        }

    def _run_id_from_key(self, key: str) -> str | None:
        root = self.prefix.rstrip("/")
        relative = key
        if root:
            prefix = f"{root}/"
            if not key.startswith(prefix):
                return None
            relative = key[len(prefix) :]
        run_id = relative.split("/", 1)[0].strip()
        return run_id or None
