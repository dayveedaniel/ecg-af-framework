# ECG Benchmark Dashboard

Local dashboard web project for `ecg-bench dashboard`. The Python package keeps a small compatibility wrapper at `ecg_benchmark.dashboard`, but the UI and server live here.

The dashboard is intentionally local-first. It reads benchmark artifacts from
MinIO/S3, optionally reads local run logs, and can queue benchmark runs from the
host or through the Spark Docker service.

## What The Dashboard Shows

- `Results`: completed benchmark runs, artifact links, metric tables, best-model
  summaries, label-distribution plots, result deletion, and a metric-vs-time
  scatter plot.
- `Explorer`: registered dataset summaries from Postgres plus supported model
  metadata from the benchmark model registry.
- `Config Builder`: form-based benchmark YAML creation, validation, browser
  download, and saving normalized YAML to the MinIO/S3 config prefix.
- `Run Experiment`: package-example, MinIO/S3, or uploaded YAML configs;
  one-at-a-time execution queue; live stage list and progress bar; deletion of
  queued or finished dashboard jobs.
- `Cache`: reusable split/window cache entries, dataset filtering, manifest
  settings, size/object counts, and cache-entry deletion.

Opening a dashboard tab refreshes the data for tabs that have a Refresh button,
so switching back to Results, Explorer, Run Experiment, or Cache pulls current
artifact/job/cache state.

The metric-vs-time graph uses metrics from `metrics/*_metrics.csv` and timings
from `timings/timings.json`. The time axis can use evaluation/inference time or
training plus evaluation time. Rule models that do not require training are
recorded with `train.duration_seconds = 0`, while their actual fit/save wall
time remains available as `training_wall_seconds` in dashboard data.

## Quick Start

Run these commands from the repository root.

For the common local stack, use the master script:

```bash
./scripts/start_ecg_af_stack.sh
```

It starts Postgres, MinIO, pgAdmin, the Spark/Jupyter container, and the local
dashboard with the Docker-backed experiment runner. It also prints service URLs,
the dashboard PID file, and the dashboard log path.

Useful script flags:

```bash
SKIP_UV_SYNC=1 ./scripts/start_ecg_af_stack.sh
WITH_AIRFLOW=1 ./scripts/start_ecg_af_stack.sh
KEEP_DASHBOARD=1 ./scripts/start_ecg_af_stack.sh
```

Stop the script-managed dashboard and Docker services:

```bash
./scripts/stop_ecg_af_stack.sh
```

1. Install the benchmark package dependencies:

```bash
uv sync --project py_ecg_benchmark_pkg
```

2. Make sure the local environment file exists:

```bash
test -f data-pipeline/.env || cp data-pipeline/.env.example data-pipeline/.env
```

3. Export the dashboard connection settings:

```bash
set -a
source data-pipeline/.env
set +a
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1"
export no_proxy="${no_proxy:+$no_proxy,}localhost,127.0.0.1"
```

4. Start MinIO/Postgres if they are not already running:

```bash
test -f data-pipeline/.env.docker || cp data-pipeline/.env.docker.example data-pipeline/.env.docker
(cd data-pipeline && ./scripts/dev_stack.sh .env.docker)
```

5. Start the dashboard:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --host 127.0.0.1 \
  --port 8765 \
  --log-dir experiments/data_pipeline_test
```

Then open:

```text
http://127.0.0.1:8765
```

The default artifact location is:

```text
s3://ecg-pipeline/benchmarks/runs/
```

The default reusable cache location is:

```text
s3://ecg-pipeline/benchmarks/cache/
```

Use `--artifact-bucket`, `--artifact-prefix`, and `--cache-prefix` if your
benchmark results or reusable partitions were written somewhere else.

The Run Experiment tab can launch benchmark configs from package examples,
MinIO/S3 config files, or local YAML uploads. One experiment runs at a time;
additional submissions are queued and can be removed before they start.

A streamlined dashboard flow is:

1. Use Explorer to confirm dataset availability and model ids.
2. Use Config Builder to create and validate a config.
3. Download the YAML locally or save it to `s3://<artifact_bucket>/<s3_config_prefix>/`.
4. Use Run Experiment with the package, MinIO/S3, or upload source.

Package examples are the YAML files in `py_ecg_benchmark_pkg/examples/`. They
are useful quick selections for the canonical baseline benchmark, showcase
smoke run, full showcase run, and overnight showcase run.

If host-side Java is unavailable, run dashboard-launched experiments inside the
Spark notebook container:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --host 127.0.0.1 \
  --port 8765 \
  --log-dir experiments/data_pipeline_test \
  --runner-backend docker-compose \
  --runner-job-dir experiments/data_pipeline_test/dashboard_jobs
```

This keeps the dashboard on the host, but queued runs execute as:

```text
docker compose ... exec -T spark-eda python -m ecg_benchmark.cli run ...
```

The job directory must be under `experiments/data_pipeline_test` because that
folder is mounted into `spark-eda` at `/home/jovyan/work`.

## Common Commands

Run with the default artifact bucket and prefix:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard
```

Run on another port:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard --port 8766
```

Point at a different artifact location:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --artifact-bucket ecg-pipeline \
  --artifact-prefix benchmarks/runs \
  --cache-prefix benchmarks/cache \
  --s3-config-prefix benchmarks/configs
```

Scan more than one local log directory for elapsed-time metadata:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --log-dir experiments/data_pipeline_test \
  --log-dir /path/to/other/logs
```

Run queued experiments inside a different Compose service:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --runner-backend docker-compose \
  --runner-job-dir experiments/data_pipeline_test/dashboard_jobs \
  --docker-service spark-eda \
  --docker-compose-file data-pipeline/docker-compose.yaml \
  --docker-compose-file experiments/data_pipeline_test/docker-compose.spark.yaml \
  --docker-env-file data-pipeline/.env.docker \
  --docker-container-job-dir /home/jovyan/work/dashboard_jobs
```

Stop the dashboard with `Ctrl+C` in the terminal where it is running. This stops
the dashboard process and its local queue worker; it does not stop Docker
services or delete benchmark artifacts.

Stop the Docker stack separately when needed, or use
`./scripts/stop_ecg_af_stack.sh` to stop both the script-managed dashboard and
Docker services:

```bash
docker compose \
  --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f experiments/data_pipeline_test/docker-compose.spark.yaml \
  down --remove-orphans
```

## Running Experiments From The Dashboard

The dashboard can load config YAML from three sources:

- `Package example`: a file under `py_ecg_benchmark_pkg/examples/`.
- `S3 config`: a `.yaml`, `.yml`, or `.json` object under
  `s3://<artifact_bucket>/<s3_config_prefix>/`.
- `Upload`: YAML selected from the browser and staged into the dashboard job
  directory.

Before launching a job, the dashboard parses the YAML with the same config
validation used by the CLI. It also rewrites the preview into the normalized
resolved YAML form, so defaults and aliases are visible before submission.

The Config Builder can save normalized YAML directly to the configured
`--s3-config-prefix`. Saved configs appear in the Run Experiment tab under
`S3 config` after refreshing or reopening that tab.

One job runs at a time. Additional jobs stay queued and can be removed before
they start. A running job can be cancelled, which terminates the child process
and marks the job cancelled if the process exits from the termination signal.

Dashboard job state is local to the dashboard process and job directory. Each
job directory contains:

```text
job.json
<config-name>.yaml
progress.jsonl
stdout.log
stderr.log
```

For local runs the default job directory is
`py_ecg_benchmark_pkg/generated_configs/dashboard_jobs/`. For Docker-backed
runs, use `experiments/data_pipeline_test/dashboard_jobs/` so the same files are
visible inside the `spark-eda` container at `/home/jovyan/work/dashboard_jobs`.

## Runner Backends

`--runner-backend local` launches:

```text
python -m ecg_benchmark.cli run --config <staged-config> --progress-file <progress-file>
```

Use it when the host environment has Java, PySpark, package dependencies, and
host-style connection settings such as `S3_ENDPOINT_URL=http://localhost:9000`.

`--runner-backend docker-compose` launches the same CLI inside a Compose
service:

```text
docker compose ... exec -T spark-eda python -m ecg_benchmark.cli run ...
```

Use it when manual benchmark runs already work inside `spark-eda` or when the
host does not have Java/Spark configured. The job directory must be mounted into
the service and `--docker-container-job-dir` must point at the matching
container path.

## Cache Tab

The Cache tab lists objects under
`s3://<artifact_bucket>/<cache_prefix>/splits/...` and
`s3://<artifact_bucket>/<cache_prefix>/features/...`.

Use the dataset filter when you only want to inspect cache entries for one
dataset. Deleting a cache entry removes all objects below that one key. It does
not delete historical run metrics, matrices, model artifacts, or predictions,
but deleting feature cache entries can make older run manifests that point at
the deleted parquet files unusable for future re-training or re-evaluation.

## What It Needs

- MinIO/S3 credentials from `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`.
- Benchmark artifacts under `s3://<artifact_bucket>/<artifact_prefix>/<run_id>/`.
- Optional reusable split/window cache entries under `s3://<artifact_bucket>/<cache_prefix>/`.
- Optional runnable config files under `s3://<artifact_bucket>/<s3_config_prefix>/`.
- Optional Postgres access for Config Builder dataset choices. If Postgres is unavailable, the builder still works with manual dataset names.
- Optional local log files for nicer started/finished/elapsed-time display.
- The Config Builder can download YAML files from the browser or save normalized
  configs to MinIO/S3 under `--s3-config-prefix`.
- Docker runner mode needs the Compose stack already running and the host job directory mounted into the selected service.
- Timing artifacts are optional, but the metric-vs-time graph is most useful for
  runs created after timing support was added.

## Troubleshooting

- If the page opens but no runs appear, confirm MinIO is running and the artifact bucket/prefix match the run output.
- If the Cache tab is empty, confirm the benchmark was run with `reuse.enabled: true` and the dashboard `--cache-prefix` matches the config's `storage.cache_prefix`.
- If package examples are missing, start the dashboard from the repository root
  or verify `py_ecg_benchmark_pkg/examples/` exists in the checkout. The runner
  searches both the package path and the repository-root examples path.
- If package examples appear but S3 configs do not, either upload a YAML file in the Run Experiment tab or place configs under the prefix passed with `--s3-config-prefix`.
- If local YAML upload fails at `Load config`, check that PyYAML is installed in
  the environment running the dashboard. `uv sync --project py_ecg_benchmark_pkg`
  installs it.
- If a dashboard-launched run fails quickly but the same config works manually in
  Docker, restart the dashboard with `--runner-backend docker-compose` and a job
  directory under `experiments/data_pipeline_test`.
- If a dashboard-launched experiment fails with a Java or PySpark gateway error, install Java on the host and set `JAVA_HOME`, or run the benchmark from the `spark-eda` Docker service. The dashboard runner auto-fills `JAVA_HOME` when it can infer it from `java` on `PATH`.
- The Run Experiment queue is local to the dashboard process. Staged configs,
  progress JSONL, and stdout/stderr logs are stored under `--runner-job-dir`
  when provided, otherwise under
  `py_ecg_benchmark_pkg/generated_configs/dashboard_jobs/`.
- Deleting a feature cache entry removes the parquet files. Runs whose feature manifests point at that entry may no longer be readable for re-training or re-evaluation.
- If the Config Builder cannot load dataset options, check the `POSTGRES_*` variables or enter dataset names manually.
- If port `8765` is already in use, start the server with `--port 8766`.
- If S3 requests fail from the host, make sure `S3_ENDPOINT_URL=http://localhost:9000` and `NO_PROXY/no_proxy` include `localhost,127.0.0.1`.

## Structure

- `server.py` - HTTP server, route handling, and app launcher.
- `services.py` - S3/MinIO run discovery and dashboard data aggregation.
- `config_builder.py` - config presets, validation, YAML generation, and config file naming helpers.
- `runner.py` - experiment queue, subprocess launcher, config-source loading, and progress parsing.
- `results.py` - run log parsing, metric pivoting, and result summaries.
- `templates/index.html` - dashboard shell markup.
- `static/styles.css` - dashboard styling.
- `static/app.js` - browser-side dashboard and config-builder behavior.
