from __future__ import annotations

from types import SimpleNamespace

from ecg_benchmark.config import BenchmarkConfig, ConfigError
from ecg_benchmark.storage import S3ArtifactStore


def test_config_defaults_and_hash_are_stable() -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"]},
        }
    )
    config.validate()

    assert config.data.target_datasets == ["dataset-a"]
    assert config.data.window_seconds == 10
    assert config.data.stride_seconds == 5
    assert config.data.label_policy == "af_vs_nsr"
    assert config.data.recording_aggregation == "mean"
    assert config.data.recording_label_policy == "fraction_threshold"
    assert config.data.recording_label_threshold == 0.5
    assert config.data.split.stratify_by == "af_present"
    assert config.storage.artifact_bucket == "ecg-pipeline"
    assert config.storage.cache_prefix == "benchmarks/cache"
    assert config.reuse.enabled is True
    assert config.reuse.splits is True
    assert config.reuse.features is True
    assert config.reuse.mode == "auto"
    assert config.config_hash() == BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"]},
        }
    ).config_hash()


def test_random_seed_is_required() -> None:
    try:
        BenchmarkConfig.from_mapping({"data": {"source_datasets": ["dataset-a"]}})
    except ConfigError as exc:
        assert "random_seed is required" in str(exc)
    else:
        raise AssertionError("Expected ConfigError")


def test_raw_cnn_model_id_is_supported_but_not_default() -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"]},
            "models": [{"id": "raw_cnn_1d"}],
        }
    )
    config.validate()

    assert [model.id for model in config.models] == ["raw_cnn_1d"]

    default_config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {"source_datasets": ["dataset-a"]},
        }
    )

    assert "raw_cnn_1d" not in [model.id for model in default_config.models]


def test_recording_label_and_split_stratification_options_are_supported() -> None:
    config = BenchmarkConfig.from_mapping(
        {
            "random_seed": 42,
            "data": {
                "source_datasets": ["dataset-a"],
                "recording_label_policy": "any",
                "recording_label_threshold": 0.2,
                "split": {"stratify_by": "dataset_af_present"},
            },
        }
    )
    config.validate()

    assert config.data.recording_label_policy == "any"
    assert config.data.recording_label_threshold == 0.2
    assert config.data.split.stratify_by == "dataset_af_present"


def test_window_label_policy_options_are_supported() -> None:
    for policy in ["af_vs_nsr", "af_vs_non_af", "af_or_afl_vs_nsr", "af_or_afl_vs_non_af"]:
        config = BenchmarkConfig.from_mapping(
            {
                "random_seed": 42,
                "data": {
                    "source_datasets": ["dataset-a"],
                    "label_policy": policy,
                },
            }
        )
        config.validate()
        assert config.data.label_policy == policy


def test_s3_store_from_env_uses_configured_env_names(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_init(self, *, endpoint_url, access_key, secret_key, region_name=None):
        del self, region_name
        captured["endpoint_url"] = endpoint_url
        captured["access_key"] = access_key
        captured["secret_key"] = secret_key

    monkeypatch.setattr(S3ArtifactStore, "__init__", fake_init)
    monkeypatch.setenv("ALT_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("ALT_S3_ACCESS_KEY", "access")
    monkeypatch.setenv("ALT_S3_SECRET_KEY", "secret")

    S3ArtifactStore.from_env(
        SimpleNamespace(
            endpoint_env="ALT_S3_ENDPOINT",
            access_key_env="ALT_S3_ACCESS_KEY",
            secret_key_env="ALT_S3_SECRET_KEY",
        )
    )

    assert captured == {
        "endpoint_url": "http://minio:9000",
        "access_key": "access",
        "secret_key": "secret",
    }
