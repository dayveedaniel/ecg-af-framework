from __future__ import annotations

import datetime as dt
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from dashboard.settings import BEST_METRIC_PRIORITY, DEFAULT_LOG_DIRS
from dashboard.types import ArtifactObject


def parse_run_log(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"path": str(path), "status": "unknown"}
    started = _regex_group(r"^started\s+(.+)$", text)
    finished = _regex_group(r"^finished\s+(\S+)", text)
    exit_code_text = _regex_group(r"^finished\s+\S+\s+exit_code=(\d+)", text)
    run_id = _regex_group(r"^run_id\s+(\S+)", text) or _regex_group(r'"run_id"\s*:\s*"([^"]+)"', text)
    started_at = _parse_datetime_text(started)
    finished_at = _parse_datetime_text(finished)
    exit_code = _parse_int(exit_code_text, default=None)
    elapsed_seconds = None
    if started_at and finished_at:
        elapsed_seconds = max(0.0, (finished_at - started_at).total_seconds())
    status = "unknown"
    if exit_code == 0:
        status = "succeeded"
    elif exit_code is not None:
        status = "failed"
    return {
        "path": str(path),
        "run_id": run_id,
        "started_at": _isoformat(started_at),
        "finished_at": _isoformat(finished_at),
        "elapsed_seconds": elapsed_seconds,
        "elapsed_label": format_duration(elapsed_seconds),
        "exit_code": exit_code,
        "status": status,
    }


def parse_job_file(path: str | Path) -> dict[str, Any]:
    """Read dashboard job metadata as run-log-like status information."""

    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "status": "unknown"}
    if not isinstance(payload, dict):
        return {"path": str(path), "status": "unknown"}

    run_id = str(payload.get("run_id") or "").strip() or None
    started_at = _isoformat(_parse_datetime_text(payload.get("started_at")))
    finished_at = _isoformat(_parse_datetime_text(payload.get("finished_at")))
    elapsed_seconds = elapsed_between(started_at, finished_at)
    status = _normalize_status(payload.get("status"))
    return {
        "path": str(path),
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed_seconds,
        "elapsed_label": format_duration(elapsed_seconds),
        "exit_code": _parse_int(payload.get("exit_code"), default=None),
        "status": status,
        "job_id": payload.get("job_id"),
        "config_name": payload.get("config_name"),
        "error": payload.get("error"),
    }


def summarize_config(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    experiment = _mapping(config.get("experiment"))
    data = _mapping(config.get("data"))
    models = config.get("models") or []
    model_ids = []
    for item in models if isinstance(models, list) else []:
        if isinstance(item, dict):
            model_ids.append(str(item.get("id", "")))
        else:
            model_ids.append(str(item))
    model_ids = [item for item in model_ids if item]
    return {
        "experiment_name": experiment.get("name") or "unknown experiment",
        "experiment_description": experiment.get("description"),
        "random_seed": config.get("random_seed"),
        "source_datasets": _string_list(data.get("source_datasets")),
        "target_datasets": _string_list(data.get("target_datasets")),
        "sample_rate_hz": data.get("sample_rate_hz"),
        "window_seconds": data.get("window_seconds"),
        "stride_seconds": data.get("stride_seconds"),
        "recording_aggregation": data.get("recording_aggregation"),
        "recording_label_policy": data.get("recording_label_policy"),
        "models": model_ids,
        "model_count": len(model_ids),
        "metrics": _string_list(config.get("metrics")),
    }


def summarize_artifacts(objects: list[ArtifactObject]) -> dict[str, Any]:
    timestamps = [obj.last_modified for obj in objects if obj.last_modified is not None]
    first = min(timestamps) if timestamps else None
    last = max(timestamps) if timestamps else None
    return {
        "artifact_count": len(objects),
        "artifact_size_bytes": sum(max(0, obj.size) for obj in objects),
        "first_artifact_at": _isoformat(first),
        "last_artifact_at": _isoformat(last),
    }


def pivot_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        metric_name = str(row.get("metric_name") or "").strip()
        if not metric_name:
            continue
        key = (
            str(row.get("evaluation_level") or ""),
            str(row.get("source_dataset") or ""),
            str(row.get("target_dataset") or ""),
            str(row.get("model_id") or ""),
        )
        grouped.setdefault(
            key,
            {
                "evaluation_level": key[0],
                "source_dataset": key[1],
                "target_dataset": key[2],
                "model_id": key[3],
            },
        )[metric_name] = _coerce_number(row.get("metric_value"))
    return [grouped[key] for key in sorted(grouped)]


def attach_timing_rows(comparison_rows: list[dict[str, Any]], timing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    train_by_model: dict[tuple[str, str], dict[str, Any]] = {}
    target_by_stage: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in timing_rows:
        stage = str(row.get("stage") or "").strip()
        duration = _coerce_number(row.get("duration_seconds"))
        source_dataset = str(row.get("source_dataset") or "")
        target_dataset = str(row.get("target_dataset") or "")
        model_id = str(row.get("model_id") or "")
        if not stage or duration is None or not source_dataset or not model_id:
            continue
        if stage == "train":
            train_by_model[(source_dataset, model_id)] = row
        elif target_dataset:
            target_by_stage[(stage, source_dataset, target_dataset, model_id)] = row

    attached: list[dict[str, Any]] = []
    for row in comparison_rows:
        source_dataset = str(row.get("source_dataset") or "")
        target_dataset = str(row.get("target_dataset") or "")
        model_id = str(row.get("model_id") or "")
        train = train_by_model.get((source_dataset, model_id))
        evaluate = target_by_stage.get(("evaluate", source_dataset, target_dataset, model_id))
        inference = target_by_stage.get(("inference", source_dataset, target_dataset, model_id))
        enriched = dict(row)
        train_seconds = _coerce_number(train.get("duration_seconds")) if train else None
        evaluation_seconds = _coerce_number(evaluate.get("duration_seconds")) if evaluate else None
        inference_seconds = _coerce_number(inference.get("duration_seconds")) if inference else None
        if train_seconds is not None:
            enriched["train_seconds"] = train_seconds
            enriched["training_row_count"] = _coerce_number(train.get("row_count"))
            enriched["validation_row_count"] = _coerce_number(train.get("validation_row_count"))
            if "requires_training" in train:
                enriched["requires_training"] = bool(train.get("requires_training"))
            wall_seconds = _coerce_number(train.get("wall_duration_seconds"))
            if wall_seconds is not None:
                enriched["training_wall_seconds"] = wall_seconds
        if evaluation_seconds is not None:
            enriched["evaluation_seconds"] = evaluation_seconds
            enriched["evaluation_row_count"] = _coerce_number(evaluate.get("row_count"))
        if inference_seconds is not None:
            enriched["inference_seconds"] = inference_seconds
            enriched["inference_row_count"] = _coerce_number(inference.get("row_count"))
        if train_seconds is not None and evaluation_seconds is not None:
            enriched["train_evaluation_seconds"] = train_seconds + evaluation_seconds
        input_kind = _first_non_empty(inference, "input_kind") or _first_non_empty(evaluate, "input_kind") or _first_non_empty(train, "input_kind")
        if input_kind:
            enriched["input_kind"] = input_kind
        attached.append(enriched)
    return attached


def best_metrics(metrics: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any] | None]:
    best: dict[str, dict[str, Any] | None] = {}
    for level, rows in metrics.items():
        pivoted = pivot_metric_rows(rows)
        best[level] = None
        for metric_name in BEST_METRIC_PRIORITY:
            candidates = [row for row in pivoted if _is_number(row.get(metric_name))]
            if candidates:
                winner = max(candidates, key=lambda row: float(row[metric_name]))
                best[level] = {
                    "metric_name": metric_name,
                    "metric_value": winner[metric_name],
                    "model_id": winner.get("model_id"),
                    "source_dataset": winner.get("source_dataset"),
                    "target_dataset": winner.get("target_dataset"),
                }
                break
    return best


def status_from_artifacts(metrics: dict[str, list[dict[str, Any]]], objects: list[ArtifactObject]) -> str:
    if any(metrics.values()):
        return "succeeded"
    if objects:
        return "incomplete"
    return "unknown"


def label_distribution_rows(comparison_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build target-cohort label-composition rows from pivoted metric rows."""

    grouped: dict[tuple[str, str, float, float, float], dict[str, Any]] = {}
    for row in comparison_rows:
        n = _coerce_number(row.get("n"))
        positive = _coerce_number(row.get("positive"))
        negative = _coerce_number(row.get("negative"))
        if n is None:
            if positive is None and negative is None:
                continue
            n = float(positive or 0) + float(negative or 0)
        if positive is None and negative is not None:
            positive = max(0.0, float(n) - float(negative))
        if negative is None and positive is not None:
            negative = max(0.0, float(n) - float(positive))
        if positive is None or negative is None:
            continue
        key = (
            str(row.get("evaluation_level") or ""),
            str(row.get("target_dataset") or ""),
            float(n),
            float(positive),
            float(negative),
        )
        grouped.setdefault(
            key,
            {
                "evaluation_level": key[0],
                "target_dataset": key[1],
                "n": key[2],
                "positive": key[3],
                "negative": key[4],
                "positive_fraction": (key[3] / key[2]) if key[2] else None,
                "source_datasets": set(),
                "model_ids": set(),
                "input_kinds": set(),
                "row_count": 0,
            },
        )
        grouped[key]["row_count"] += 1
        if row.get("source_dataset"):
            grouped[key]["source_datasets"].add(str(row["source_dataset"]))
        if row.get("model_id"):
            grouped[key]["model_ids"].add(str(row["model_id"]))
        if row.get("input_kind"):
            grouped[key]["input_kinds"].add(str(row["input_kind"]))

    rows: list[dict[str, Any]] = []
    for key in sorted(grouped):
        item = dict(grouped[key])
        item["source_datasets"] = sorted(item["source_datasets"])
        item["model_ids"] = sorted(item["model_ids"])
        item["input_kinds"] = sorted(item["input_kinds"])
        rows.append(item)
    return rows


def elapsed_between(started_at: str | None, finished_at: str | None) -> float | None:
    started = _parse_datetime_text(started_at)
    finished = _parse_datetime_text(finished_at)
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds())


def format_duration(seconds: float | int | None) -> str | None:
    if seconds is None:
        return None
    seconds = int(round(float(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _default_log_dirs(log_dirs: Iterable[str | Path] | None) -> list[Path]:
    candidates = list(log_dirs) if log_dirs is not None else list(DEFAULT_LOG_DIRS)
    paths: list[Path] = []
    seen: set[Path] = set()
    for item in candidates:
        path = Path(item).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _summary_sort_key(summary: dict[str, Any]) -> tuple[str, str]:
    return (
        str(summary.get("finished_at") or summary.get("last_artifact_at") or ""),
        str(summary.get("run_id") or ""),
    )


def _regex_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _parse_datetime_text(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _isoformat(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _latest_datetime(left: Any, right: dt.datetime | None) -> dt.datetime | None:
    if not isinstance(left, dt.datetime):
        return right
    if right is None:
        return left
    return max(left, right)


def _parse_int(value: Any, *, default: int | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_number(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_number(value: Any) -> bool:
    return _coerce_number(value) is not None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _first_non_empty(mapping: dict[str, Any] | None, key: str) -> Any:
    if not mapping:
        return None
    value = mapping.get(key)
    return value if value not in {None, ""} else None


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"succeeded", "failed", "cancelled", "removed", "running", "queued"}:
        return status
    return "unknown"
