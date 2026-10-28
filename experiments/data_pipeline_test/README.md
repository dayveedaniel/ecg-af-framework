# Data Pipeline Spark EDA

Best approach for your setup: reuse the existing `data-pipeline` Docker stack and add a small experiments-specific compose override.

Why:
- You already run MinIO in `data-pipeline/docker-compose.yaml`.
- Processed parquet data is written to MinIO under `processed/250hz/...`.
- Reusing the same stack avoids duplicate env files and duplicated service definitions.
- The Spark notebook container is configured with the Hadoop S3A connector, so notebooks can read `s3a://ecg-pipeline/processed/...` directly.

## Start Spark notebook (reusing data-pipeline stack)

From repo root:

```bash
test -f data-pipeline/.env.docker || cp data-pipeline/.env.docker.example data-pipeline/.env.docker

docker compose \
  --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f experiments/data_pipeline_test/docker-compose.spark.yaml \
  up -d postgres minio minio-init spark-eda
```

Open:
- `http://localhost:8889/lab?token=ecg`

The default notebook token is `ecg`; override it by setting `JUPYTER_TOKEN` before starting the compose stack.

Notebook paths:
- `experiments/data_pipeline_test/01_fetch_and_eda_processed_250hz.ipynb`
  - Lists processed files in MinIO.
  - Reads ECG parquet and annotation JSON sidecars with Spark.
  - Prints schemas and sample rows for each processed dataset.
- `experiments/data_pipeline_test/af_benchmark_framework_demo.ipynb`
  - Demonstrates the new `ecg_benchmark` package from source.
  - Loads the example benchmark config and artifact layout.
  - Reads real processed data from MinIO using the same Spark/S3A pattern.
  - Builds a small RR-interval baseline demo from annotation sidecars.
  - Uses a recording-disjoint train/test split for the real-data smoke test.
  - Explains why window-level and recording-level metrics should be interpreted differently.

## Benchmark CLI and dashboard from this stack

The `spark-eda` container installs `py_ecg_benchmark_pkg[raw]`, sets
`PYTHONPATH` to the live package source, includes Java/Spark, and uses Docker
network connection settings. That makes it the recommended environment for
benchmark commands that build Spark windows or train raw-waveform models.

Run a smoke benchmark inside the container:

```bash
docker compose \
  --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f experiments/data_pipeline_test/docker-compose.spark.yaml \
  exec -T spark-eda \
  python -m ecg_benchmark.cli run \
  --config /home/jovyan/py_ecg_benchmark_pkg/examples/showcase_smoke.yaml
```

Run the host dashboard while executing queued jobs inside this same container:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
  --host 127.0.0.1 \
  --port 8765 \
  --log-dir experiments/data_pipeline_test \
  --runner-backend docker-compose \
  --runner-job-dir experiments/data_pipeline_test/dashboard_jobs
```

Open `http://127.0.0.1:8765`. The dashboard job directory is mounted into the
container at `/home/jovyan/work/dashboard_jobs`.

More benchmark and dashboard docs:

- `py_ecg_benchmark_pkg/README.md`
- `py_ecg_benchmark_pkg/docs/config_reference.md`
- `py_ecg_benchmark_pkg/docs/artifacts.md`
- `dashboard/README.md`

Notes:
- The Spark notebook container is configured with `PYSPARK_SUBMIT_ARGS=--packages org.apache.hadoop:hadoop-aws:3.3.4 pyspark-shell` so `s3a://...` works from notebooks.
- The compose override builds a thin `ecg-af-spark-eda` image that installs `py_ecg_benchmark_pkg[raw]`, including Postgres, sklearn, and PyTorch dependencies used by the CLI smoke tests.
- The override mounts `py_ecg_benchmark_pkg` read-only into the notebook container and sets `PYTHONPATH=/home/jovyan/py_ecg_benchmark_pkg/src`, so `import ecg_benchmark` uses the live source tree in the benchmark demo.
- The same container also exposes the `ecg-bench` console script.
- Inside the container, `S3_ENDPOINT_URL=http://minio:9000` is correct. From host-side scripts, use `http://localhost:9000`.
- On first startup, Docker builds the notebook image and Spark may spend extra
  time downloading jars before the first session is created. After Dockerfile or
  dependency changes, add `--build` to the `docker compose up` command.

## Stop

If you used the repo-level stack helper, stop the dashboard and Docker services
with:

```bash
./scripts/stop_ecg_af_stack.sh
```

To stop only the Compose services manually:

```bash
docker compose \
  --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f experiments/data_pipeline_test/docker-compose.spark.yaml \
  down --remove-orphans
```
