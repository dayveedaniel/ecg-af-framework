"""AF detection benchmark framework.

The package contains the reusable pieces behind the ``ecg-bench`` CLI:
configuration validation, deterministic splits, window labeling, baseline
models, metrics, and artifact path handling.
"""

from ecg_benchmark.config import BenchmarkConfig, ConfigError, load_config

__all__ = ["BenchmarkConfig", "ConfigError", "load_config"]

