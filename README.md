# ECG-AF

Research workspace for atrial fibrillation detection on ECG data. The repository
contains the data ingestion pipeline, the reusable benchmark package, the local
dashboard, and notebook-based development checks.

Framework code is licensed under [MIT](LICENSE). Source datasets retain their
respective licenses and attribution requirements; see [Publishing](docs/PUBLISHING.md).

The processed dataset is available separately at
[dayveedd/ecg-af-250hz](https://huggingface.co/datasets/dayveedd/ecg-af-250hz).
The framework can also download supported datasets from their original sources
using the included manifests; publishing or downloading the Hub collection is
not required for that workflow.

## Repository Map

- `data-pipeline/`: dataset download, processing, annotation enrichment, MinIO
  storage, and Postgres catalog registration.
- `py_ecg_benchmark_pkg/`: benchmark framework, model API, CLI, configs,
  artifact layout, metrics, reusable partition/window cache, and tests.
- `dashboard/`: local web dashboard used by `ecg-bench dashboard`.
- `experiments/data_pipeline_test/`: Spark notebook container and notebooks for
  checking processed data and demonstrating the benchmark on real data.

## Quick Start

This project uses [uv](https://github.com/astral-sh/uv) for Python dependency
management. Run commands from the repository root unless noted otherwise.

For a machine where Docker and `uv` are already installed, the quickest local
startup path is:

```bash
./scripts/start_ecg_af_stack.sh
```

That script starts Postgres, MinIO, pgAdmin, the Spark/Jupyter container, and
the local dashboard, then prints the service links and dashboard log location.

Stop the full local stack gracefully:

```bash
./scripts/stop_ecg_af_stack.sh
```

```bash
uv sync --project data-pipeline
uv sync --project py_ecg_benchmark_pkg
uv sync --project experiments
```

Start local MinIO and Postgres:

```bash
test -f data-pipeline/.env.docker || cp data-pipeline/.env.docker.example data-pipeline/.env.docker
(cd data-pipeline && ./scripts/dev_stack.sh .env.docker)
```

For host-side benchmark CLI or dashboard commands, load the local environment:

```bash
test -f data-pipeline/.env || cp data-pipeline/.env.example data-pipeline/.env
set -a
source data-pipeline/.env
set +a
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1"
export no_proxy="${no_proxy:+$no_proxy,}localhost,127.0.0.1"
```

Run a benchmark smoke config:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench run \
  --config py_ecg_benchmark_pkg/examples/showcase_smoke.yaml
```

Start the dashboard:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --host 127.0.0.1 \
  --port 8765 \
  --log-dir experiments/data_pipeline_test
```

Open `http://127.0.0.1:8765`.

## Documentation

- Dataset publication and framework release: `docs/PUBLISHING.md`
- Data pipeline: `data-pipeline/README.md`
- Dataset onboarding: `data-pipeline/ADDING_DATASET.md`
- Spark notebooks and demo container: `experiments/data_pipeline_test/README.md`
- Benchmark guide: `py_ecg_benchmark_pkg/README.md`
- Benchmark config reference: `py_ecg_benchmark_pkg/docs/config_reference.md`
- Model API and included models: `py_ecg_benchmark_pkg/docs/model_api.md`
- Artifact, cache, timing, and progress reference:
  `py_ecg_benchmark_pkg/docs/artifacts.md`
- Dashboard guide: `dashboard/README.md`

## Local Service Ports

- MinIO API: `http://localhost:9000`
- MinIO console: `http://localhost:9001`
- Postgres: `http://localhost:5432`
- Spark/Jupyter demo container: `http://localhost:8889/lab?token=ecg`
- Dashboard: `http://127.0.0.1:8765`

## Notes

The benchmark expects processed ECG parquet and annotation sidecars in MinIO and
recording metadata in Postgres. Use the data pipeline first when a dataset has
not yet been downloaded, processed, and registered.
