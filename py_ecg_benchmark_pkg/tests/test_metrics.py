from __future__ import annotations

from ecg_benchmark.metrics import BinaryPrediction, aggregate_by_recording, compute_binary_metrics
from ecg_benchmark.workflow import _metrics_matrix_rows


def test_metrics_and_recording_aggregation() -> None:
    predictions = [
        BinaryPrediction(1, 0.9, "a"),
        BinaryPrediction(1, 0.7, "a"),
        BinaryPrediction(0, 0.2, "b"),
        BinaryPrediction(0, 0.4, "b"),
    ]

    recording_predictions = aggregate_by_recording(predictions, policy="mean")
    metrics = compute_binary_metrics(recording_predictions, metric_names=["accuracy", "sensitivity", "specificity"])

    assert metrics == {"accuracy": 1.0, "sensitivity": 1.0, "specificity": 1.0}


def test_recording_aggregation_marks_recording_positive_if_any_window_is_positive() -> None:
    predictions = [
        BinaryPrediction(0, 0.1, "mixed"),
        BinaryPrediction(1, 0.8, "mixed"),
        BinaryPrediction(0, 0.2, "negative"),
    ]

    recording_predictions = aggregate_by_recording(predictions, policy="mean")
    by_id = {item.recording_id: item for item in recording_predictions}

    assert by_id["mixed"].y_true == 1
    assert by_id["negative"].y_true == 0


def test_recording_label_fraction_threshold_can_keep_mixed_recordings_negative() -> None:
    predictions = [
        BinaryPrediction(0, 0.1, "mixed"),
        BinaryPrediction(1, 0.8, "mixed"),
        BinaryPrediction(0, 0.2, "mixed"),
    ]

    recording_predictions = aggregate_by_recording(
        predictions,
        policy="mean",
        label_policy="fraction_threshold",
        label_threshold=0.5,
    )

    assert recording_predictions[0].y_true == 0


def test_default_metrics_include_label_counts() -> None:
    metrics = compute_binary_metrics(
        [
            BinaryPrediction(1, 0.9),
            BinaryPrediction(0, 0.1),
            BinaryPrediction(0, 0.4),
        ]
    )

    assert metrics["n"] == 3.0
    assert metrics["positive"] == 1.0
    assert metrics["negative"] == 2.0


def test_metrics_matrix_rows_pivots_one_evaluation_level() -> None:
    rows = [
        {
            "source_dataset": "source-a",
            "target_dataset": "target-a",
            "model_id": "dummy_prior",
            "evaluation_level": "window",
            "metric_name": "accuracy",
            "metric_value": 0.5,
        },
        {
            "source_dataset": "source-a",
            "target_dataset": "target-a",
            "model_id": "dummy_prior",
            "evaluation_level": "window",
            "metric_name": "f1",
            "metric_value": 0.25,
        },
    ]

    assert _metrics_matrix_rows(rows) == [
        {
            "source_dataset": "source-a",
            "target_dataset": "target-a",
            "model_id": "dummy_prior",
            "accuracy": 0.5,
            "f1": 0.25,
        }
    ]
