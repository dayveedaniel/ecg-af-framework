from __future__ import annotations

from types import SimpleNamespace

from ecg_benchmark.cache import feature_cache_key, split_cache_key
from ecg_benchmark.config import BenchmarkConfig
from ecg_benchmark.splits import RecordingRecord


def test_split_cache_key_ignores_models_and_metrics() -> None:
    base = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"]},
            "models": [{"id": "dummy_prior"}],
            "metrics": ["auroc"],
        }
    )
    changed_models = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"]},
            "models": [{"id": "random_forest_features"}],
            "metrics": ["f1", "accuracy"],
        }
    )
    records = [
        RecordingRecord("dataset-a", "r1", subject_id="s1", af_present=True),
        RecordingRecord("dataset-a", "r2", subject_id="s2", af_present=False),
    ]

    assert split_cache_key(base, "dataset-a", records) == split_cache_key(changed_models, "dataset-a", records)


def test_split_cache_key_changes_with_seed() -> None:
    first = BenchmarkConfig.from_mapping({"random_seed": 1, "data": {"source_datasets": ["dataset-a"]}})
    second = BenchmarkConfig.from_mapping({"random_seed": 2, "data": {"source_datasets": ["dataset-a"]}})
    records = [RecordingRecord("dataset-a", "r1", subject_id="s1", af_present=True)]

    assert split_cache_key(first, "dataset-a", records) != split_cache_key(second, "dataset-a", records)


def test_feature_cache_key_changes_with_windowing_not_models() -> None:
    base = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"], "window_seconds": 10, "stride_seconds": 5},
            "models": [{"id": "dummy_prior"}],
        }
    )
    changed_model = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"], "window_seconds": 10, "stride_seconds": 5},
            "models": [{"id": "raw_cnn_1d"}],
        }
    )
    changed_stride = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"], "window_seconds": 10, "stride_seconds": 10},
        }
    )
    recordings = [
        SimpleNamespace(
            dataset_name="dataset-a",
            full_recording_id="r1",
            subject_id="s1",
            af_present=True,
            sampling_rate=250,
            duration_seconds=60.0,
            channel_names=["II"],
            parquet_path="s3://ecg-pipeline/processed/250hz/dataset-a/r1.parquet",
            annotations_path="s3://ecg-pipeline/processed/250hz/dataset-a/r1.annotations.json",
        )
    ]

    assert feature_cache_key(base, "dataset-a", recordings) == feature_cache_key(changed_model, "dataset-a", recordings)
    assert feature_cache_key(base, "dataset-a", recordings) != feature_cache_key(changed_stride, "dataset-a", recordings)
