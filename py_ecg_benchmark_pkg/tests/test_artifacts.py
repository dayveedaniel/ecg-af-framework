from __future__ import annotations

from ecg_benchmark.artifacts import ArtifactLayout
from ecg_benchmark.config import BenchmarkConfig


def test_artifact_layout_uses_expected_paths() -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 7,
            "data": {"source_datasets": ["dataset-a"]},
        }
    )
    layout = ArtifactLayout.from_config(config, "run 1")

    assert layout.run_uri == "s3://ecg-pipeline/benchmarks/runs/run-1/"
    assert layout.split_manifest_uri() == "s3://ecg-pipeline/benchmarks/runs/run-1/splits/split_manifest.json"
    assert (
        layout.model_uri("source-a", "dummy_prior")
        == "s3://ecg-pipeline/benchmarks/runs/run-1/models/source-a/dummy_prior"
    )
    assert (
        layout.waveform_windows_uri("dataset-a")
        == "s3://ecg-pipeline/benchmarks/runs/run-1/features/dataset-a/waveform_windows.parquet"
    )
    assert (
        layout.feature_cache_manifest_uri("dataset-a", "abc123")
        == "s3://ecg-pipeline/benchmarks/cache/features/dataset-a/abc123/feature_manifest.json"
    )
    assert (
        layout.evaluation_metrics_csv_uri("window")
        == "s3://ecg-pipeline/benchmarks/runs/run-1/metrics/window_metrics.csv"
    )
    assert (
        layout.evaluation_metrics_csv_uri("recording")
        == "s3://ecg-pipeline/benchmarks/runs/run-1/metrics/recording_metrics.csv"
    )
    assert layout.timings_json_uri() == "s3://ecg-pipeline/benchmarks/runs/run-1/timings/timings.json"
    assert layout.timings_csv_uri() == "s3://ecg-pipeline/benchmarks/runs/run-1/timings/timings.csv"
    assert (
        layout.evaluation_matrix_csv_uri("window")
        == "s3://ecg-pipeline/benchmarks/runs/run-1/matrix/window_benchmark_matrix.csv"
    )
    assert (
        layout.evaluation_matrix_csv_uri("recording")
        == "s3://ecg-pipeline/benchmarks/runs/run-1/matrix/recording_benchmark_matrix.csv"
    )
