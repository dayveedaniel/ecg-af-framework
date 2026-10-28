from __future__ import annotations

import json
import math
import pickle
import tempfile
from pathlib import Path
from typing import Any

from ecg_benchmark.artifacts import parse_s3_uri
from ecg_benchmark.models.base import FeatureRows
from ecg_benchmark.storage import S3ArtifactStore


IDENTITY_COLUMNS = {
    "label",
    "dataset_name",
    "recording_id",
    "full_recording_id",
    "recording_name",
    "window_index",
    "window_start_seconds",
    "window_end_seconds",
    "start_sample",
    "end_sample",
    "split",
    "subject_id",
    "rhythm",
    "lead_column",
    "lead_name",
    "parquet_path",
    "annotations_path",
}


def label(row: dict[str, Any]) -> int:
    if "label" not in row:
        raise KeyError("Feature rows must contain a 'label' column")
    value = int(row["label"])
    if value not in {0, 1}:
        raise ValueError(f"Labels must be 0 or 1, got: {value}")
    return value


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def numeric_value(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key, default)
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def resolve_feature_columns(rows: FeatureRows, configured: Any) -> list[str]:
    if configured:
        if not isinstance(configured, list):
            raise ValueError("feature_columns must be a list when provided")
        return [str(item) for item in configured]
    columns: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key in IDENTITY_COLUMNS or key in columns:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                columns.append(key)
    if not columns:
        raise ValueError("No numeric feature columns found")
    return sorted(columns)


def matrix(rows: FeatureRows, columns: list[str]) -> list[list[float]]:
    return [[numeric_value(row, column) for column in columns] for row in rows]


def class_prior(rows: FeatureRows) -> float:
    labels = [label(row) for row in rows]
    return (sum(labels) / len(labels)) if labels else 0.0


def fit_threshold(values: list[float], labels: list[int], *, default: float) -> float:
    pairs: list[tuple[float, int]] = []
    for value, truth in zip(values, labels):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            pairs.append((number, int(truth)))
    if not pairs or len({truth for _, truth in pairs}) < 2:
        return float(default)

    groups: list[tuple[float, int, int]] = []
    for value, truth in sorted(pairs, key=lambda item: item[0]):
        if groups and groups[-1][0] == value:
            group_value, positives, negatives = groups[-1]
            groups[-1] = (
                group_value,
                positives + (1 if truth == 1 else 0),
                negatives + (1 if truth == 0 else 0),
            )
        else:
            groups.append((value, 1 if truth == 1 else 0, 1 if truth == 0 else 0))

    total_positives = sum(positives for _, positives, _ in groups)
    total_negatives = sum(negatives for _, _, negatives in groups)
    if not total_positives or not total_negatives:
        return float(default)

    def balanced_accuracy(true_positives: int, true_negatives: int) -> float:
        sensitivity = true_positives / total_positives
        specificity = true_negatives / total_negatives
        return (sensitivity + specificity) / 2.0

    best_threshold = groups[0][0]
    best_score = balanced_accuracy(total_positives, 0)
    left_positives = 0
    left_negatives = 0
    for index, (value, positives, negatives) in enumerate(groups[:-1]):
        left_positives += positives
        left_negatives += negatives
        threshold = (value + groups[index + 1][0]) / 2.0
        score = balanced_accuracy(total_positives - left_positives, left_negatives)
        if score > best_score:
            best_score = score
            best_threshold = threshold

    value, positives, negatives = groups[-1]
    left_positives += positives
    left_negatives += negatives
    score = balanced_accuracy(total_positives - left_positives + positives, left_negatives - negatives)
    if score > best_score:
        best_threshold = value
    return float(best_threshold)


def save_json(s3_uri: str, payload: dict[str, Any], filename: str = "model.json") -> None:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(artifact_key_uri(s3_uri, filename))
    store.put_json(parsed.bucket, parsed.key, payload)


def load_json(s3_uri: str, filename: str = "model.json") -> dict[str, Any]:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(artifact_key_uri(s3_uri, filename))
    return json.loads(store.get_bytes(parsed.bucket, parsed.key).decode("utf-8"))


def save_pickle(s3_uri: str, payload: dict[str, Any], filename: str = "model.pkl") -> None:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(artifact_key_uri(s3_uri, filename))
    with tempfile.TemporaryDirectory(prefix="ecg-bench-model-save-") as tmpdir:
        path = Path(tmpdir) / filename
        path.write_bytes(pickle.dumps(payload))
        store.upload_file(parsed.bucket, parsed.key, path)


def load_pickle(s3_uri: str, filename: str = "model.pkl") -> dict[str, Any]:
    store = S3ArtifactStore.from_env()
    parsed = parse_s3_uri(artifact_key_uri(s3_uri, filename))
    return pickle.loads(store.get_bytes(parsed.bucket, parsed.key))


def artifact_key_uri(s3_uri: str, filename: str) -> str:
    return f"{s3_uri.rstrip('/')}/{filename}"


def _balanced_accuracy(labels: list[int], predictions: list[int]) -> float:
    positives = sum(1 for value in labels if value == 1)
    negatives = len(labels) - positives
    tp = sum(1 for truth, pred in zip(labels, predictions) if truth == 1 and pred == 1)
    tn = sum(1 for truth, pred in zip(labels, predictions) if truth == 0 and pred == 0)
    sensitivity = tp / positives if positives else 0.0
    specificity = tn / negatives if negatives else 0.0
    return (sensitivity + specificity) / 2.0
