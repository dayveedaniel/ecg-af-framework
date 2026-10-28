from __future__ import annotations

from ecg_benchmark.splits import RecordingRecord, make_deterministic_splits


def test_splits_are_reproducible_and_subject_safe() -> None:
    records = [
        RecordingRecord("d", "r1", subject_id="s1", af_present=True),
        RecordingRecord("d", "r2", subject_id="s1", af_present=True),
        RecordingRecord("d", "r3", subject_id="s2", af_present=False),
        RecordingRecord("d", "r4", subject_id="s3", af_present=False),
    ]

    first = make_deterministic_splits(records, seed=123, train_ratio=0.5, val_ratio=0.25, test_ratio=0.25)
    second = make_deterministic_splits(records, seed=123, train_ratio=0.5, val_ratio=0.25, test_ratio=0.25)

    assert first == second
    subject_s1_splits = {item.split for item in first if item.full_recording_id in {"r1", "r2"}}
    assert len(subject_s1_splits) == 1


def test_dataset_stratification_splits_each_dataset_independently() -> None:
    records = [
        RecordingRecord("dataset-a", f"a{i}", af_present=True)
        for i in range(4)
    ] + [
        RecordingRecord("dataset-b", f"b{i}", af_present=True)
        for i in range(4)
    ]

    assignments = make_deterministic_splits(
        records,
        seed=123,
        train_ratio=0.5,
        val_ratio=0.25,
        test_ratio=0.25,
        stratify_by="dataset",
    )
    by_dataset = {
        dataset: {item.split for item in assignments if item.dataset_name == dataset}
        for dataset in ["dataset-a", "dataset-b"]
    }

    assert by_dataset == {
        "dataset-a": {"train", "val", "test"},
        "dataset-b": {"train", "val", "test"},
    }
