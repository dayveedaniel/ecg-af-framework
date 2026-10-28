from __future__ import annotations

from ecg_benchmark.labels import RhythmEpisode, episodes_from_annotation_json, label_window, labels_for_recording


def test_label_window_keeps_only_fully_inside_af_or_nsr() -> None:
    episodes = [
        RhythmEpisode(0, 10, "NSR"),
        RhythmEpisode(10, 20, "AF"),
        RhythmEpisode(20, 30, "AFL"),
    ]

    assert label_window(episodes, window_start=0, window_end=10).label == 0
    assert label_window(episodes, window_start=10, window_end=20).label == 1
    assert label_window(episodes, window_start=5, window_end=15).label is None
    assert label_window(episodes, window_start=20, window_end=30).label is None


def test_label_window_supports_non_af_negative_policy() -> None:
    episodes = [
        RhythmEpisode(0, 10, "AF"),
        RhythmEpisode(10, 20, "AFL"),
        RhythmEpisode(20, 30, "NSR"),
        RhythmEpisode(30, 40, "OTHER"),
        RhythmEpisode(40, 50, "UNKNOWN"),
    ]

    assert label_window(episodes, window_start=0, window_end=10, policy="af_vs_non_af").label == 1
    assert label_window(episodes, window_start=10, window_end=20, policy="af_vs_non_af").label == 0
    assert label_window(episodes, window_start=20, window_end=30, policy="af_vs_non_af").label == 0
    assert label_window(episodes, window_start=30, window_end=40, policy="af_vs_non_af").label == 0
    assert label_window(episodes, window_start=40, window_end=50, policy="af_vs_non_af").label is None


def test_label_window_supports_af_or_afl_positive_policies() -> None:
    episodes = [
        RhythmEpisode(0, 10, "AFL"),
        RhythmEpisode(10, 20, "OTHER"),
        RhythmEpisode(20, 30, "NSR"),
    ]

    assert label_window(episodes, window_start=0, window_end=10, policy="af_or_afl_vs_nsr").label == 1
    assert label_window(episodes, window_start=10, window_end=20, policy="af_or_afl_vs_nsr").label is None
    assert label_window(episodes, window_start=20, window_end=30, policy="af_or_afl_vs_nsr").label == 0
    assert label_window(episodes, window_start=0, window_end=10, policy="af_or_afl_vs_non_af").label == 1
    assert label_window(episodes, window_start=10, window_end=20, policy="af_or_afl_vs_non_af").label == 0


def test_label_window_drops_overlapping_positive_and_negative_rhythms() -> None:
    episodes = [
        RhythmEpisode(0, 10, "AF"),
        RhythmEpisode(0, 10, "NSR"),
    ]

    label = label_window(episodes, window_start=0, window_end=10, policy="af_vs_nsr")

    assert label.label is None
    assert label.reason == "ambiguous_overlapping_rhythm"


def test_labels_for_recording_uses_window_and_stride() -> None:
    labels = labels_for_recording(
        duration_seconds=20,
        episodes=[RhythmEpisode(0, 20, "AF")],
        window_seconds=10,
        stride_seconds=5,
    )

    assert [(item.start_seconds, item.end_seconds, item.label) for item in labels] == [
        (0.0, 10.0, 1),
        (5.0, 15.0, 1),
        (10.0, 20.0, 1),
    ]


def test_episodes_from_pipeline_annotation_json_uses_sample_indexes() -> None:
    episodes = episodes_from_annotation_json(
        {
            "target_sampling_rate": 250,
            "rhythm_episodes": [
                {"start_sample": 0, "end_sample": 2500, "rhythm_type": "AF"},
            ],
        }
    )

    assert episodes == [RhythmEpisode(0.0, 10.0, "AF")]
