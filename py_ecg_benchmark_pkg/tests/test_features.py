from __future__ import annotations

from ecg_benchmark.features import numeric_window_features, select_single_lead


def test_select_single_lead_prefers_configured_lead() -> None:
    selection = select_single_lead(["sample_index", "channel_0_I", "channel_1_II"], ["II", "I"])

    assert selection.column_name == "channel_1_II"
    assert selection.reason == "preferred"


def test_numeric_window_features_include_rr_cv() -> None:
    features = numeric_window_features([1.0, 2.0, 3.0], rr_intervals_seconds=[1.0, 1.2, 0.8])

    assert features["signal_mean"] == 2.0
    assert features["rr_count"] == 3.0
    assert features["rr_cv"] > 0

