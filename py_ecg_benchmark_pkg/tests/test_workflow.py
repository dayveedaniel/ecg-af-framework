from __future__ import annotations

from types import SimpleNamespace

import ecg_benchmark.workflow as workflow
from ecg_benchmark.config import BenchmarkConfig
from ecg_benchmark.spark_features import FeatureBuildResult


class FakeCatalog:
    def ensure_schema(self) -> None:
        pass

    def recordings_for_datasets(self, datasets, *, sample_rate_hz):
        del sample_rate_hz
        return [
            SimpleNamespace(
                dataset_name=dataset,
                full_recording_id=f"{dataset}/r1",
                subject_id="s1",
                af_present=True,
                sampling_rate=250,
                duration_seconds=60.0,
                channel_names=["II"],
                parquet_path=f"s3://ecg-pipeline/processed/250hz/{dataset}/r1.parquet",
                annotations_path=f"s3://ecg-pipeline/processed/250hz/{dataset}/r1.annotations.json",
            )
            for dataset in datasets
        ]

    def close(self) -> None:
        pass


class FakeStore:
    def __init__(self) -> None:
        self.json: dict[tuple[str, str], object] = {}
        self.text: dict[tuple[str, str], str] = {}

    def put_json(self, bucket: str, key: str, payload, *, metadata=None) -> None:
        del metadata
        self.json[(bucket, key)] = payload

    def put_text(self, bucket: str, key: str, text: str, *, content_type=None) -> None:
        del content_type
        self.text[(bucket, key)] = text


def test_build_features_writes_summary_from_result_dicts(monkeypatch) -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 7,
            "data": {"source_datasets": ["dataset-a"], "target_datasets": ["dataset-a"]},
            "reuse": {"enabled": False},
        }
    )
    store = FakeStore()

    monkeypatch.setattr(workflow.BenchmarkCatalog, "from_env", lambda database: FakeCatalog())
    monkeypatch.setattr(workflow.S3ArtifactStore, "from_env", lambda storage=None: store)
    monkeypatch.setattr(
        workflow,
        "build_features_with_spark",
        lambda config, layout, recordings, feature_roots=None, progress=None: [
            FeatureBuildResult(
                dataset_name="dataset-a",
                feature_uri="s3://ecg-pipeline/benchmarks/runs/run-1/features/dataset-a",
                waveform_uri="s3://ecg-pipeline/benchmarks/runs/run-1/features/dataset-a/waveform_windows.parquet",
                source_recordings=len(recordings),
                labeled_windows=5,
                windows_uri="s3://ecg-pipeline/benchmarks/runs/run-1/features/dataset-a/windows.parquet",
            )
        ],
    )

    results = workflow.build_features(config, run_id="run-1")

    assert results[0]["feature_uri"] == "s3://ecg-pipeline/benchmarks/runs/run-1/features/dataset-a"
    summary_text = store.text[("ecg-pipeline", "benchmarks/runs/run-1/RUN_SUMMARY.md")]
    assert "s3://ecg-pipeline/benchmarks/runs/run-1/features/dataset-a" in summary_text


def test_limit_evaluation_rows_is_deterministic() -> None:
    rows = [
        {"full_recording_id": "r1", "window_index": index, "window_start_seconds": float(index)}
        for index in range(20)
    ]

    first = workflow._limit_evaluation_rows(rows, 5, seed_parts=["7", "src", "tgt", "model"])
    second = workflow._limit_evaluation_rows(rows, 5, seed_parts=["7", "src", "tgt", "model"])

    assert first == second
    assert len(first) == 5
    assert [row["window_index"] for row in first] == sorted(row["window_index"] for row in first)


def test_evaluation_limit_seed_is_independent_of_source_and_model() -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 7,
            "data": {"source_datasets": ["dataset-a"]},
        }
    )

    assert workflow._evaluation_limit_seed_parts(
        config,
        target_dataset="target",
        input_kind="waveform",
        limit=2048,
    ) == ["7", "target", "waveform", "2048"]


def test_configured_storage_env_mirrors_custom_names(monkeypatch) -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 7,
            "data": {"source_datasets": ["dataset-a"]},
            "storage": {
                "endpoint_env": "ALT_S3_ENDPOINT",
                "access_key_env": "ALT_S3_ACCESS_KEY",
                "secret_key_env": "ALT_S3_SECRET_KEY",
            },
        }
    )
    monkeypatch.setenv("ALT_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("ALT_S3_ACCESS_KEY", "access")
    monkeypatch.setenv("ALT_S3_SECRET_KEY", "secret")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)

    with workflow._configured_storage_env(config):
        assert workflow.os.environ["S3_ENDPOINT_URL"] == "http://minio:9000"
        assert workflow.os.environ["S3_ACCESS_KEY"] == "access"
        assert workflow.os.environ["S3_SECRET_KEY"] == "secret"

    assert "S3_ENDPOINT_URL" not in workflow.os.environ
    assert "S3_ACCESS_KEY" not in workflow.os.environ
    assert "S3_SECRET_KEY" not in workflow.os.environ
