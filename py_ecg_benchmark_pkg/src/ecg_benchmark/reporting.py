from __future__ import annotations

import csv
import io
from typing import Any

from ecg_benchmark.artifacts import ArtifactLayout
from ecg_benchmark.config import BenchmarkConfig
from ecg_benchmark.models import info_as_dict, model_infos


def config_reference_markdown() -> str:
    """Generate a compact Markdown reference for benchmark configs."""

    model_lines = []
    for info in model_infos():
        params = ", ".join(sorted(info.configurable_parameters)) or "none"
        model_lines.append(f"- `{info.model_id}` ({info.input_kind}): {info.description} Parameters: {params}.")
    return "\n".join(
        [
            "# Benchmark Config Reference",
            "",
            "Required top-level field: `random_seed`.",
            "",
            "Defaults:",
            "- `data.window_seconds`: `10`",
            "- `data.stride_seconds`: `5`",
            "- `data.label_policy`: `af_vs_nsr` (`af_vs_non_af`, `af_or_afl_vs_nsr`, and `af_or_afl_vs_non_af` are also supported)",
            "- `data.lead_policy`: `single_preferred_lead`",
            "- `data.recording_aggregation`: `mean`",
            "- `data.recording_label_policy`: `fraction_threshold`",
            "- `data.recording_label_threshold`: `0.5`",
            "- `data.split.stratify_by`: `af_present`",
            "- `storage.artifact_bucket`: `ecg-pipeline`",
            "- `storage.artifact_prefix`: `benchmarks/runs`",
            "- `storage.cache_prefix`: `benchmarks/cache`",
            "- `reuse.enabled`: `true`",
            "- `reuse.mode`: `auto`",
            "",
            "Artifacts are written under `s3://<artifact_bucket>/<artifact_prefix>/<run_id>/`.",
            "Reusable split and feature cache entries are written under `s3://<artifact_bucket>/<cache_prefix>/`.",
            "",
            "Models:",
            *model_lines,
            "",
        ]
    )


def run_summary_markdown(
    *,
    config: BenchmarkConfig,
    layout: ArtifactLayout,
    split_uri: str | None = None,
    feature_uris: list[str] | None = None,
) -> str:
    feature_uris = feature_uris or []
    lines = [
        f"# Benchmark Run: {layout.run_id}",
        "",
        f"- Experiment: `{config.experiment.name}`",
        f"- Random seed: `{config.random_seed}`",
        f"- Config hash: `{config.config_hash()}`",
        f"- Artifact root: `{layout.run_uri}`",
        f"- Resolved config: `{layout.resolved_config_uri()}`",
        f"- Config reference snapshot: `{layout.config_reference_uri()}`",
    ]
    if split_uri:
        lines.append(f"- Split manifest: `{split_uri}`")
    for uri in feature_uris:
        lines.append(f"- Feature dataset: `{uri}`")
    lines.extend(
        [
            f"- Window metrics JSON: `{layout.evaluation_metrics_json_uri('window')}`",
            f"- Window metrics CSV: `{layout.evaluation_metrics_csv_uri('window')}`",
            f"- Recording metrics JSON: `{layout.evaluation_metrics_json_uri('recording')}`",
            f"- Recording metrics CSV: `{layout.evaluation_metrics_csv_uri('recording')}`",
            f"- Timing JSON: `{layout.timings_json_uri()}`",
            f"- Timing CSV: `{layout.timings_csv_uri()}`",
            f"- Window matrix CSV: `{layout.evaluation_matrix_csv_uri('window')}`",
            f"- Recording matrix CSV: `{layout.evaluation_matrix_csv_uri('recording')}`",
            f"- Primary/window metrics JSON: `{layout.metrics_json_uri()}`",
            f"- Primary/window metrics CSV: `{layout.metrics_csv_uri()}`",
            f"- Primary/window matrix CSV: `{layout.matrix_csv_uri()}`",
            "",
        ]
    )
    return "\n".join(lines)


def metrics_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    output = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def model_reference() -> list[dict[str, Any]]:
    return [info_as_dict(info) for info in model_infos()]
