from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class BinaryPrediction:
    """One binary prediction with an optional recording id for aggregation."""

    y_true: int
    y_score: float
    recording_id: str | None = None


def compute_binary_metrics(
    predictions: Iterable[BinaryPrediction],
    *,
    threshold: float = 0.5,
    metric_names: list[str] | None = None,
) -> dict[str, float | None]:
    """Compute AF-vs-NSR metrics with explicit undefined handling."""

    rows = list(predictions)
    metric_names = metric_names or [
        "n",
        "positive",
        "negative",
        "auroc",
        "average_precision",
        "accuracy",
        "f1",
        "sensitivity",
        "specificity",
    ]
    y_true = [int(item.y_true) for item in rows]
    y_score = [float(item.y_score) for item in rows]
    y_pred = [1 if score >= threshold else 0 for score in y_score]
    tp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 1 and yp == 1)
    tn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 0 and yp == 0)
    fp = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 0 and yp == 1)
    fn = sum(1 for yt, yp in zip(y_true, y_pred) if yt == 1 and yp == 0)

    all_metrics: dict[str, float | None] = {
        "n": float(len(rows)),
        "positive": float(sum(y_true)),
        "negative": float(len(rows) - sum(y_true)),
        "auroc": _roc_auc(y_true, y_score),
        "average_precision": _average_precision(y_true, y_score),
        "accuracy": _safe_div(tp + tn, len(rows)),
        "precision": _safe_div(tp, tp + fp),
        "recall": _safe_div(tp, tp + fn),
        "sensitivity": _safe_div(tp, tp + fn),
        "specificity": _safe_div(tn, tn + fp),
        "npv": _safe_div(tn, tn + fn),
        "f1": _safe_div(2 * tp, (2 * tp) + fp + fn),
    }
    sensitivity = all_metrics["sensitivity"]
    specificity = all_metrics["specificity"]
    all_metrics["balanced_accuracy"] = (
        None if sensitivity is None or specificity is None else (sensitivity + specificity) / 2
    )
    return {name: all_metrics.get(name) for name in metric_names}


def aggregate_by_recording(
    predictions: Iterable[BinaryPrediction],
    *,
    policy: str = "mean",
    label_policy: str = "any",
    label_threshold: float = 0.5,
) -> list[BinaryPrediction]:
    """Aggregate window scores to recording scores."""

    groups: dict[str, list[BinaryPrediction]] = defaultdict(list)
    passthrough: list[BinaryPrediction] = []
    for item in predictions:
        if item.recording_id is None:
            passthrough.append(item)
        else:
            groups[item.recording_id].append(item)

    aggregated = list(passthrough)
    for recording_id, rows in sorted(groups.items()):
        positive_fraction = sum(row.y_true for row in rows) / len(rows)
        y_true = _recording_label(positive_fraction, label_policy, label_threshold)
        scores = [row.y_score for row in rows]
        if policy == "mean":
            score = mean(scores)
        elif policy == "max":
            score = max(scores)
        elif policy == "majority_vote":
            score = sum(1 for value in scores if value >= 0.5) / len(scores)
        else:
            raise ValueError(f"Unsupported recording aggregation policy: {policy}")
        aggregated.append(BinaryPrediction(y_true=y_true, y_score=score, recording_id=recording_id))
    return aggregated


def _recording_label(positive_fraction: float, label_policy: str, label_threshold: float) -> int:
    if label_policy == "any":
        return 1 if positive_fraction > 0 else 0
    if label_policy == "majority":
        return 1 if positive_fraction >= 0.5 else 0
    if label_policy == "fraction_threshold":
        return 1 if positive_fraction >= label_threshold else 0
    raise ValueError(f"Unsupported recording label policy: {label_policy}")


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _roc_auc(y_true: list[int], y_score: list[float]) -> float | None:
    positives = [score for truth, score in zip(y_true, y_score) if truth == 1]
    negatives = [score for truth, score in zip(y_true, y_score) if truth == 0]
    if not positives or not negatives:
        return None
    wins = 0.0
    for pos in positives:
        for neg in negatives:
            if pos > neg:
                wins += 1.0
            elif pos == neg:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def _average_precision(y_true: list[int], y_score: list[float]) -> float | None:
    positives = sum(y_true)
    if positives == 0:
        return None
    sorted_pairs = sorted(zip(y_score, y_true), key=lambda item: item[0], reverse=True)
    hits = 0
    precision_sum = 0.0
    for rank, (_, truth) in enumerate(sorted_pairs, start=1):
        if truth == 1:
            hits += 1
            precision_sum += hits / rank
    return precision_sum / positives
