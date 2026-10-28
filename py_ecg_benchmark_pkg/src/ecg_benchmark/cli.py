from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ecg_benchmark.config import ConfigError, DEFAULT_CACHE_PREFIX, load_config
from ecg_benchmark.progress import ProgressReporter
from ecg_benchmark.workflow import (
    build_features,
    evaluate_run,
    make_splits,
    run_full_workflow,
    train_model,
    validate_config_file,
    write_matrix,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        for error in exc.errors:
            print(f"config error: {error}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"runtime error: {exc}", file=sys.stderr)
        return 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecg-bench",
        description="AF detection benchmark framework. All durable outputs are written to MinIO/S3.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-config",
        help="Validate config schema, defaults, env vars, catalog datasets, and artifact destination.",
        description=(
            "Validate a benchmark YAML config. With --check-connections, the command also checks "
            "Postgres dataset names and MinIO/S3 artifact bucket access."
        ),
    )
    _add_config_arg(validate)
    validate.add_argument("--skip-env", action="store_true", help="Skip required environment variable checks.")
    validate.add_argument(
        "--check-connections",
        action="store_true",
        help="Connect to Postgres and MinIO/S3 to verify dataset names and artifact destination.",
    )
    validate.set_defaults(func=_cmd_validate_config)

    split = subparsers.add_parser(
        "make-splits",
        help="Create deterministic split manifests and write them to MinIO/S3 and Postgres.",
        description=(
            "Creates subject-safe train/val/test splits using random_seed. Output path: "
            "s3://<artifact_bucket>/<artifact_prefix>/<run_id>/splits/split_manifest.json"
        ),
    )
    _add_config_arg(split)
    split.add_argument("--run-id", help="Optional explicit run id. Defaults to timestamp-name-configHash.")
    split.set_defaults(func=_cmd_make_splits)

    features = subparsers.add_parser(
        "build-features",
        help="Build Spark-generated window/feature parquet and write it to MinIO/S3.",
        description=(
            "Builds feature artifacts under "
            "s3://<artifact_bucket>/<artifact_prefix>/<run_id>/features/<dataset>/"
        ),
    )
    _add_config_arg(features)
    features.add_argument("--run-id", required=True, help="Run id whose artifact root receives feature outputs.")
    features.set_defaults(func=_cmd_build_features)

    train = subparsers.add_parser(
        "train",
        help="Train one model on one source dataset and write model artifacts to MinIO/S3.",
        description=(
            "Intended output path: "
            "s3://<artifact_bucket>/<artifact_prefix>/<run_id>/models/<source_dataset>/<model_id>/"
        ),
    )
    _add_config_arg(train)
    train.add_argument("--run-id", required=True)
    train.add_argument("--source-dataset", required=True)
    train.add_argument("--model", required=True)
    train.set_defaults(func=_cmd_train)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate trained artifacts on target datasets and write predictions/metrics to MinIO/S3.",
        description=(
            "Reads trained artifacts and writes window predictions, recording predictions, and metrics under "
            "the run artifact root."
        ),
    )
    _add_config_arg(evaluate)
    evaluate.add_argument("--run-id", required=True)
    evaluate.set_defaults(func=_cmd_evaluate)

    matrix = subparsers.add_parser(
        "matrix",
        help="Read stored metrics and write window/recording benchmark matrices to MinIO/S3.",
        description=(
            "Intended output paths: "
            "s3://<artifact_bucket>/<artifact_prefix>/<run_id>/matrix/window_benchmark_matrix.csv and "
            "matrix/recording_benchmark_matrix.csv"
        ),
    )
    matrix.add_argument("--run-id", required=True)
    matrix.set_defaults(func=_cmd_matrix)

    dashboard = subparsers.add_parser(
        "dashboard",
        help="Serve a local dashboard for benchmark runs and metrics.",
        description=(
            "Starts a dependency-light local web UI that reads benchmark artifacts from MinIO/S3. "
            "It expects the same S3 environment variables used by the rest of ecg-bench."
        ),
    )
    dashboard.add_argument("--host", default="127.0.0.1", help="Interface to bind. Defaults to localhost.")
    dashboard.add_argument("--port", type=int, default=8765, help="Port to bind. Defaults to 8765.")
    dashboard.add_argument(
        "--artifact-bucket",
        default="ecg-pipeline",
        help="Artifact bucket to read. Defaults to ecg-pipeline.",
    )
    dashboard.add_argument(
        "--artifact-prefix",
        default="benchmarks/runs",
        help="Artifact prefix containing run directories. Defaults to benchmarks/runs.",
    )
    dashboard.add_argument(
        "--cache-prefix",
        default=DEFAULT_CACHE_PREFIX,
        help=f"Cache prefix containing reusable split/feature entries. Defaults to {DEFAULT_CACHE_PREFIX}.",
    )
    dashboard.add_argument(
        "--s3-config-prefix",
        default="benchmarks/configs",
        help="S3/MinIO prefix to list dashboard-runnable config files from. Defaults to benchmarks/configs.",
    )
    dashboard.add_argument(
        "--log-dir",
        action="append",
        default=None,
        help="Optional local directory of run logs to scan for elapsed time. Can be passed more than once.",
    )
    dashboard.add_argument(
        "--runner-backend",
        choices=("local", "docker-compose"),
        default=os.environ.get("ECG_BENCH_DASHBOARD_RUNNER_BACKEND", "local"),
        help="Where dashboard-launched experiments run. Defaults to local.",
    )
    dashboard.add_argument(
        "--runner-job-dir",
        default=os.environ.get("ECG_BENCH_DASHBOARD_RUNNER_JOB_DIR"),
        help=(
            "Directory for queued dashboard configs/progress. For docker-compose runs this must be mounted "
            "inside the runner container."
        ),
    )
    dashboard.add_argument(
        "--docker-compose-file",
        action="append",
        default=None,
        help="Compose file for docker-compose runner backend. Pass more than once to mirror the stack command.",
    )
    dashboard.add_argument(
        "--docker-env-file",
        default=os.environ.get("ECG_BENCH_DASHBOARD_DOCKER_ENV_FILE", "data-pipeline/.env.docker"),
        help="Env file passed to docker compose for docker-compose runner backend.",
    )
    dashboard.add_argument(
        "--docker-service",
        default=os.environ.get("ECG_BENCH_DASHBOARD_DOCKER_SERVICE", "spark-eda"),
        help="Compose service used by docker-compose runner backend. Defaults to spark-eda.",
    )
    dashboard.add_argument(
        "--docker-container-job-dir",
        default=os.environ.get("ECG_BENCH_DASHBOARD_DOCKER_CONTAINER_JOB_DIR", "/home/jovyan/work/dashboard_jobs"),
        help="Container path corresponding to --runner-job-dir for docker-compose runner backend.",
    )
    dashboard.set_defaults(func=_cmd_dashboard)

    run = subparsers.add_parser(
        "run",
        help="Run the benchmark workflow and write all artifacts to MinIO/S3.",
        description="Runs validate-config, make-splits, build-features, train, evaluate, and matrix in sequence.",
    )
    _add_config_arg(run)
    run.add_argument("--run-id", help="Optional explicit run id.")
    run.add_argument("--progress-file", type=Path, help="Optional JSONL file for structured workflow progress events.")
    run.set_defaults(func=_cmd_run)
    return parser


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True, type=Path, help="Path to benchmark YAML config.")


def _cmd_validate_config(args: argparse.Namespace) -> int:
    config = validate_config_file(
        args.config,
        check_env=not args.skip_env,
        check_connections=args.check_connections,
    )
    print("config ok")
    print(f"config_hash: {config.config_hash()}")
    print(f"artifact_prefix: s3://{config.storage.artifact_bucket}/{config.storage.artifact_prefix}/<run_id>/")
    return 0


def _cmd_make_splits(args: argparse.Namespace) -> int:
    config = load_config(args.config, validate=True)
    result = make_splits(config, run_id=args.run_id)
    print(json.dumps({key: result[key] for key in ["run_id", "artifact_root", "split_manifest", "split_hash"]}, indent=2))
    return 0


def _cmd_build_features(args: argparse.Namespace) -> int:
    config = load_config(args.config, validate=True)
    result = build_features(config, run_id=args.run_id)
    print(json.dumps(result, indent=2))
    return 0


def _cmd_train(args: argparse.Namespace) -> int:
    config = load_config(args.config, validate=True)
    result = train_model(
        config,
        run_id=args.run_id,
        source_dataset=args.source_dataset,
        model_id=args.model,
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    config = load_config(args.config, validate=True)
    result = evaluate_run(config, run_id=args.run_id)
    print(json.dumps({"metrics_written": len(result)}, indent=2))
    return 0


def _cmd_matrix(args: argparse.Namespace) -> int:
    matrix_uris = write_matrix(args.run_id)
    print(json.dumps({"run_id": args.run_id, "matrices": matrix_uris}, indent=2))
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    from ecg_benchmark.dashboard import serve_dashboard

    serve_dashboard(
        host=args.host,
        port=args.port,
        artifact_bucket=args.artifact_bucket,
        artifact_prefix=args.artifact_prefix,
        cache_prefix=args.cache_prefix,
        s3_config_prefix=args.s3_config_prefix,
        log_dirs=args.log_dir,
        runner_backend=args.runner_backend,
        runner_job_dir=args.runner_job_dir,
        docker_compose_files=args.docker_compose_file,
        docker_env_file=args.docker_env_file,
        docker_service=args.docker_service,
        docker_container_job_dir=args.docker_container_job_dir,
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config, validate=True)
    progress = ProgressReporter(args.progress_file) if args.progress_file else None
    result = run_full_workflow(config, run_id=args.run_id, progress=progress)
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "artifact_root": result["artifact_root"],
                "split_manifest": result["split_manifest"],
                "feature_datasets": len(result["features"]),
                "models": len(result["models"]),
                "metric_rows": len(result["metrics"]),
                "matrices": result["matrices"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
