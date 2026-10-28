from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from ecg_benchmark.catalog import BenchmarkCatalog
from ecg_benchmark.config import (
    DEFAULT_METRICS,
    DEFAULT_LABEL_POLICY,
    DEFAULT_PREFERRED_LEADS,
    DEFAULT_PROCESSED_PREFIX,
    DEFAULT_RECORDING_AGGREGATION,
    DEFAULT_RECORDING_LABEL_POLICY,
    DEFAULT_RECORDING_LABEL_THRESHOLD,
    DEFAULT_SPLIT_RATIOS,
    DEFAULT_STRIDE_SECONDS,
    DEFAULT_WINDOW_SECONDS,
    BenchmarkConfig,
    ConfigError,
    SUPPORTED_METRICS,
    load_config,
    validate_env,
)
from dashboard.settings import DEFAULT_RANDOM_SEED, EXAMPLES_DIR


def default_builder_config() -> dict[str, Any]:
    return BenchmarkConfig.from_mapping(
        {
            "random_seed": DEFAULT_RANDOM_SEED,
            "experiment": {
                "name": "custom-benchmark",
                "description": "Benchmark config created from the dashboard.",
            },
            "data": {
                "source_datasets": [],
                "target_datasets": [],
                "processed_prefix": DEFAULT_PROCESSED_PREFIX,
                "window_seconds": DEFAULT_WINDOW_SECONDS,
                "stride_seconds": DEFAULT_STRIDE_SECONDS,
                "label_policy": DEFAULT_LABEL_POLICY,
                "preferred_leads": list(DEFAULT_PREFERRED_LEADS),
                "recording_aggregation": DEFAULT_RECORDING_AGGREGATION,
                "recording_label_policy": DEFAULT_RECORDING_LABEL_POLICY,
                "recording_label_threshold": DEFAULT_RECORDING_LABEL_THRESHOLD,
                "split": {
                    "train": DEFAULT_SPLIT_RATIOS["train"],
                    "val": DEFAULT_SPLIT_RATIOS["val"],
                    "test": DEFAULT_SPLIT_RATIOS["test"],
                    "stratify_by": "dataset_af_present",
                },
            },
            "models": [],
            "metrics": list(DEFAULT_METRICS),
        }
    ).to_dict()


def config_presets() -> list[dict[str, Any]]:
    return [
        {
            "id": "custom",
            "label": "Custom",
            "description": "Start from defaults with empty datasets and no selected models.",
            "config": default_builder_config(),
        },
        _example_preset("baseline", "Baseline", "examples/benchmark.yaml", "benchmark.yaml"),
        _example_preset(
            "showcase_smoke",
            "Showcase smoke",
            "examples/showcase_smoke.yaml",
            "showcase_smoke.yaml",
        ),
    ]


def _example_preset(preset_id: str, label: str, description: str, filename: str) -> dict[str, Any]:
    path = EXAMPLES_DIR / filename
    try:
        config = load_config(path, validate=True).to_dict()
    except Exception:
        config = default_builder_config()
    return {
        "id": preset_id,
        "label": label,
        "description": description,
        "config": config,
    }


def supported_metrics_ordered() -> list[str]:
    extras = sorted(metric for metric in SUPPORTED_METRICS if metric not in DEFAULT_METRICS)
    return [*DEFAULT_METRICS, *extras]


def validate_builder_config(
    raw_config: dict[str, Any],
    *,
    check_connections: bool = False,
    store: Any | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    config: BenchmarkConfig | None = None
    try:
        config = BenchmarkConfig.from_mapping(raw_config)
        config.validate()
    except ConfigError as exc:
        errors.extend(exc.errors)
    except Exception as exc:
        errors.append(str(exc))

    if config is not None and check_connections and not errors:
        errors.extend(connection_validation_errors(config, store=store))

    if errors or config is None:
        return {
            "ok": False,
            "errors": errors or ["Config is invalid"],
            "resolved_config": config.to_dict() if config is not None else None,
            "config_hash": config.config_hash() if config is not None else None,
            "artifact_root_preview": artifact_root_preview(config) if config is not None else None,
            "yaml": config_to_yaml(config.to_dict() if config is not None else raw_config),
        }

    resolved = config.to_dict()
    return {
        "ok": True,
        "errors": [],
        "resolved_config": resolved,
        "config_hash": config.config_hash(),
        "artifact_root_preview": artifact_root_preview(config),
        "yaml": config_to_yaml(resolved),
    }


def connection_validation_errors(config: BenchmarkConfig, *, store: Any | None = None) -> list[str]:
    errors: list[str] = []
    try:
        validate_env(config)
    except ConfigError as exc:
        errors.extend(exc.errors)
        return errors

    catalog = None
    try:
        catalog = BenchmarkCatalog.from_env(config.database)
        existing = set(catalog.dataset_names())
        requested = set(config.data.source_datasets) | set(config.data.target_datasets)
        missing = sorted(requested - existing)
        errors.extend(f"Dataset not found in Postgres catalog: {name}" for name in missing)
    except Exception as exc:
        errors.append(f"Could not validate datasets against Postgres: {exc}")
    finally:
        if catalog is not None:
            catalog.close()

    raw_store = getattr(store, "raw", None)
    if raw_store is not None:
        try:
            raw_store.head_bucket(Bucket=config.storage.artifact_bucket)
        except Exception as exc:
            errors.append(f"Could not access artifact bucket '{config.storage.artifact_bucket}': {exc}")
    return errors


def artifact_root_preview(config: BenchmarkConfig) -> str:
    return f"s3://{config.storage.artifact_bucket}/{config.storage.artifact_prefix}/<run_id>/"


def config_to_yaml(payload: dict[str, Any]) -> str:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a package dependency.
        raise RuntimeError("PyYAML is required to generate config YAML") from exc

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    return yaml.dump(
        payload,
        Dumper=NoAliasDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
        width=1000,
    )


def resolve_generated_config_path(
    generated_config_dir: str | Path,
    config: dict[str, Any],
    *,
    filename: Any = None,
) -> Path:
    root = Path(generated_config_dir).expanduser().resolve()
    name = str(filename or "").strip()
    if not name:
        experiment = _mapping(config.get("experiment"))
        name = sanitize_config_filename(str(experiment.get("name") or "benchmark-config"))
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("Config filename must be a simple file name, not a path")
    if Path(name).name != name:
        raise ValueError("Config filename must not include path segments")
    if not name.endswith((".yaml", ".yml")):
        name = f"{name}.yaml"
    if not re.fullmatch(r"[A-Za-z0-9_.=-]+\.ya?ml", name):
        raise ValueError("Config filename may only contain letters, numbers, dot, underscore, equals, and dash")
    path = (root / name).resolve()
    if root not in path.parents:
        raise ValueError("Config path escapes the generated config directory")
    return path


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sanitize_config_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "-", value.strip())
    text = text.strip("-._")
    return text or "benchmark-config"


def command_examples(path: Path) -> dict[str, str]:
    quoted = shlex.quote(str(path))
    return {
        "validate": f"uv run ecg-bench validate-config --config {quoted}",
        "run": f"uv run ecg-bench run --config {quoted}",
    }


def _payload_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config", payload)
    if not isinstance(config, dict):
        raise ValueError("Request must include a config object")
    return config
