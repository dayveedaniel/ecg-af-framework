# ECG Data Pipeline (Implementation)

This folder implements the pipeline defined in `ecg_pipeline_specification_v4.md`.

Dataset onboarding guide:
- `data-pipeline/ADDING_DATASET.md`
Data layout clarification (recording vs row/user semantics):
- `data-pipeline/DATASET_INFO_CLARIFICATION.md`

## Current status

Implemented:
- Config + env loading (`ecg_pipeline.config`, `.env.example`, `pipeline_config.yaml`)
- Manifest model/validation (`ecg_pipeline.manifest`)
- Downloader module + provider strategies (`ecg_pipeline.downloader`)
- Reader/processor module with signal-first readers (`ecg_pipeline.reader`)
- Adapter-based annotation enrichment (`ecg_pipeline.annotations`)
- Postgres registration module (`ecg_pipeline.registrar`)
- Shared S3 utilities (`ecg_pipeline.utils.s3`)
- Airflow DAGs for download-only, process+register, and full end-to-end execution

## Quick start (local dev)

Run these commands from the repository root unless a command explicitly changes
directory in a subshell.

1. Install deps:
- `(cd data-pipeline && uv sync)`
- For local benchmark CLI experiments in this environment, include the optional
  benchmark dependencies with `(cd data-pipeline && uv sync --extra benchmark)`.

2. Create local env file:
- `test -f data-pipeline/.env || cp data-pipeline/.env.example data-pipeline/.env`
- `export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1"`
- `export no_proxy="${no_proxy:+$no_proxy,}localhost,127.0.0.1"`

3. Validate a manifest:
- `(cd data-pipeline && uv run python scripts/validate_manifest.py manifests/mit-bih-atrial-fibrillation-database.yaml)`

4. Start local services (MinIO + Postgres):
- `test -f data-pipeline/.env.docker || cp data-pipeline/.env.docker.example data-pipeline/.env.docker`
- Edit `data-pipeline/.env.docker` if you want non-default creds/ports (defaults are suitable for local dev).
- `(cd data-pipeline && ./scripts/dev_stack.sh .env.docker)`

5. Download one dataset:
- `(cd data-pipeline && uv run python scripts/download_dataset.py --env-file .env --manifest manifests/mit-bih-atrial-fibrillation-database.yaml)`

6. Process one downloaded dataset:
- `(cd data-pipeline && uv run python scripts/process_dataset.py --env-file .env --manifest manifests/mit-bih-atrial-fibrillation-database.yaml)`

7. Run end-to-end for one dataset:
- `(cd data-pipeline && uv run python scripts/run_dataset_pipeline.py --env-file .env --manifest manifests/mit-bih-atrial-fibrillation-database.yaml)`

## Airflow

Bring up Airflow UI (LocalExecutor) on port `8080`:
- `(cd data-pipeline && docker compose --env-file .env.docker --profile airflow -f docker-compose.yaml up -d postgres minio minio-init airflow-init airflow-webserver airflow-scheduler)`

Available DAGs:
- `ecg_download_dataset`
Purpose: `load_manifest -> download_extract_prune`
Use when you only want raw files refreshed.
- `ecg_process_register_dataset`
Purpose: `load_manifest -> process_dataset -> register_results`
Use when raw files already exist and you want to reprocess/re-register.
- `ecg_dataset_pipeline`
Purpose: `load_manifest -> download_extract_prune -> process_dataset -> register_results`
Use for full end-to-end runs.

Notes:
- All DAGs support dynamic mapping across datasets (`all_manifests=true` by default).
- Set `all_manifests=false` and provide `dataset_name` in trigger JSON for single-dataset runs.
- `process_dataset` uses manifest-selected annotation adapters and excludes adapter-declared sidecar files from waveform discovery.

pgAdmin (view tables) on port `5050`:
- `cd data-pipeline && open http://localhost:5050`
- default login in `.env.docker`: `admin@example.com` / `admin`

## Benchmark And Dashboard Integration

The benchmark package reads the outputs produced here:

- processed ECG parquet under `s3://ecg-pipeline/processed/<rate>hz/<dataset>/`;
- annotation JSON sidecars next to the processed parquet;
- recording metadata registered in Postgres.

After at least one dataset has been processed and registered, run the benchmark
from `py_ecg_benchmark_pkg/` or the Spark demo container. The main docs are:

- `py_ecg_benchmark_pkg/README.md`
- `py_ecg_benchmark_pkg/docs/config_reference.md`
- `py_ecg_benchmark_pkg/docs/artifacts.md`
- `dashboard/README.md`

Host-side benchmark and dashboard commands should use
`S3_ENDPOINT_URL=http://localhost:9000`. Commands inside Docker services should
use `S3_ENDPOINT_URL=http://minio:9000`.

The repo-level helpers start and stop the benchmark/dashboard development stack:

- `./scripts/start_ecg_af_stack.sh`
- `./scripts/stop_ecg_af_stack.sh`
