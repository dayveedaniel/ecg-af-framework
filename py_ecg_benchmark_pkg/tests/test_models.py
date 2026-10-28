from __future__ import annotations

from ecg_benchmark.models import create_model
from ecg_benchmark.models._utils import fit_threshold
from ecg_benchmark.models.raw_cnn import _limit_rows


def test_dummy_prior_predicts_training_prevalence() -> None:
    model = create_model("dummy_prior")
    model.fit(
        [{"label": 1}, {"label": 0}, {"label": 1}],
        [],
        {},
    )

    assert model.predict_proba([{"label": 0}, {"label": 1}]) == [2 / 3, 2 / 3]


def test_rr_irregularity_rule_scores_higher_for_more_irregular_windows() -> None:
    model = create_model("rr_irregularity_rule")
    model.fit([], [], {"threshold": 0.1, "slope": 10.0})
    low, high = model.predict_proba([{"rr_cv": 0.01}, {"rr_cv": 0.5}])

    assert high > low


def test_sklearn_models_consume_feature_columns_config() -> None:
    rows = [
        {"label": 0, "rr_cv": 0.01, "rr_mean": 0.9, "ignored_numeric": 100.0},
        {"label": 0, "rr_cv": 0.02, "rr_mean": 0.91, "ignored_numeric": 101.0},
        {"label": 1, "rr_cv": 0.30, "rr_mean": 0.7, "ignored_numeric": -100.0},
        {"label": 1, "rr_cv": 0.35, "rr_mean": 0.72, "ignored_numeric": -101.0},
    ]
    config = {"feature_columns": ["rr_cv", "rr_mean"], "random_state": 7}

    for model_id in ["logistic_regression_features", "random_forest_features"]:
        model = create_model(model_id).fit(rows, [], config)

        assert model.feature_columns == ["rr_cv", "rr_mean"]
        assert len(model.predict_proba(rows)) == len(rows)


def test_sklearn_default_features_exclude_window_position_metadata() -> None:
    rows = [
        {
            "label": 0,
            "full_recording_id": "rec-1",
            "window_index": 0,
            "window_start_seconds": 0.0,
            "window_end_seconds": 10.0,
            "start_sample": 0,
            "end_sample": 2500,
            "rr_cv": 0.01,
            "signal_std": 0.2,
        },
        {
            "label": 1,
            "full_recording_id": "rec-2",
            "window_index": 10,
            "window_start_seconds": 50.0,
            "window_end_seconds": 60.0,
            "start_sample": 12500,
            "end_sample": 15000,
            "rr_cv": 0.30,
            "signal_std": 0.4,
        },
    ]

    model = create_model("logistic_regression_features").fit(rows, [], {})

    assert model.feature_columns == ["rr_cv", "signal_std"]


def test_raw_cnn_model_is_registered_without_importing_torch() -> None:
    model = create_model("raw_cnn_1d")

    assert model.info.input_kind == "waveform"
    assert "parquet_path" in model.info.required_columns
    assert "lead_column" in model.info.required_columns
    assert "max_eval_windows" in model.info.configurable_parameters


def test_raw_waveform_row_limit_is_deterministic_and_balanced() -> None:
    rows = [{"id": index, "label": index % 2} for index in range(20)]

    first = _limit_rows(rows, 6, seed=7)
    second = _limit_rows(rows, 6, seed=7)

    assert first == second
    assert len(first) == 6
    assert sum(row["label"] for row in first) == 3


def test_showcase_rr_models_score_more_irregular_windows_higher() -> None:
    train_rows = [
        {"label": 0, "rr_cv": 0.02, "rr_mean": 0.9, "rr_std": 0.02, "rr_count": 12},
        {"label": 0, "rr_cv": 0.03, "rr_mean": 0.88, "rr_std": 0.03, "rr_count": 12},
        {"label": 1, "rr_cv": 0.30, "rr_mean": 0.7, "rr_std": 0.21, "rr_count": 14},
        {"label": 1, "rr_cv": 0.35, "rr_mean": 0.72, "rr_std": 0.25, "rr_count": 14},
    ]
    low = {"rr_cv": 0.02, "rr_mean": 0.9, "rr_std": 0.02, "rr_count": 12}
    high = {"rr_cv": 0.35, "rr_mean": 0.7, "rr_std": 0.25, "rr_count": 14}

    for model_id in [
        "tateno_glass_rr_histogram",
        "lake_moorman_cosen",
        "petrenas_low_complexity_detector",
    ]:
        model = create_model(model_id).fit(train_rows, [], {})
        low_score, high_score = model.predict_proba([low, high])

        assert high_score > low_score


def test_fit_threshold_matches_brute_force_threshold_search() -> None:
    values = [0.01, 0.03, 0.02, 0.30, 0.35, 0.28]
    labels = [0, 0, 0, 1, 1, 1]

    assert fit_threshold(values, labels, default=0.18) == _brute_force_threshold(values, labels, default=0.18)


def test_fit_threshold_handles_many_unique_values() -> None:
    values = [index / 100_000 for index in range(50_000)]
    labels = [0 if index < 25_000 else 1 for index in range(50_000)]

    threshold = fit_threshold(values, labels, default=0.18)

    assert 0.249 <= threshold <= 0.251


def test_showcase_feature_models_fit_small_tabular_data() -> None:
    rows = [
        {"label": 0, "rr_cv": 0.01, "rr_mean": 0.9, "signal_std": 0.1},
        {"label": 0, "rr_cv": 0.02, "rr_mean": 0.91, "signal_std": 0.11},
        {"label": 1, "rr_cv": 0.30, "rr_mean": 0.7, "signal_std": 0.4},
        {"label": 1, "rr_cv": 0.35, "rr_mean": 0.72, "signal_std": 0.42},
    ]
    params = {
        "feature_columns": ["rr_cv", "rr_mean", "signal_std"],
        "n_estimators": 5,
        "random_state": 7,
    }

    for model_id in [
        "zabihi_rf_handcrafted",
        "bin_adaboost_tree_ensemble",
        "goodfellow_xgboost_multifeature",
    ]:
        config = dict(params)
        if model_id == "bin_adaboost_tree_ensemble":
            config["max_depth"] = 1
        if model_id == "goodfellow_xgboost_multifeature":
            config["backend"] = "sklearn"
            config["max_depth"] = 1
        model = create_model(model_id).fit(rows, [], config)

        assert len(model.predict_proba(rows)) == len(rows)


def test_showcase_waveform_models_are_registered_without_importing_torch() -> None:
    for model_id in [
        "xiong_skip_cnn_1d",
        "zihlmann_crnn_spectrogram",
        "cpsc2021_resnet_transformer",
    ]:
        model = create_model(model_id)

        assert model.info.input_kind == "waveform"
        assert "parquet_path" in model.info.required_columns
        assert "max_eval_windows" in model.info.configurable_parameters


def _brute_force_threshold(values: list[float], labels: list[int], *, default: float) -> float:
    if not values or len(set(labels)) < 2:
        return float(default)
    candidates = sorted(set(float(value) for value in values))
    thresholds = [candidates[0]]
    thresholds.extend((left + right) / 2.0 for left, right in zip(candidates, candidates[1:]))
    thresholds.append(candidates[-1])
    best_threshold = thresholds[0]
    best_score = -1.0
    for threshold in thresholds:
        predictions = [1 if value >= threshold else 0 for value in values]
        positives = sum(1 for value in labels if value == 1)
        negatives = len(labels) - positives
        tp = sum(1 for truth, pred in zip(labels, predictions) if truth == 1 and pred == 1)
        tn = sum(1 for truth, pred in zip(labels, predictions) if truth == 0 and pred == 0)
        sensitivity = tp / positives if positives else 0.0
        specificity = tn / negatives if negatives else 0.0
        score = (sensitivity + specificity) / 2.0
        if score > best_score:
            best_score = score
            best_threshold = threshold
    return float(best_threshold)
