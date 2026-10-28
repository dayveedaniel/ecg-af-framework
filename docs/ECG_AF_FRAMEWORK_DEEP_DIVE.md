# ECG-AF Framework Deep Dive

This document describes the ECG-AF repository as an end-to-end research framework. It intentionally covers the repository outside `docs/thesis`, and is written so that a technical reader can present the project in depth: what happens at each step, what tools are used, why those tools were chosen, what alternatives exist, and where the current implementation has important tradeoffs.

## 1. Executive Mental Model

The repository is a local-first framework for building and evaluating atrial fibrillation detection experiments across heterogeneous public ECG datasets.

At a high level, the system has five layers:

1. **Dataset ingestion**: dataset manifests describe where each dataset lives, how it should be downloaded, and which annotation adapter knows how to interpret its labels.
2. **Signal normalization**: raw files in many formats are converted into a common processed representation: sample-indexed Parquet signal files plus JSON annotation sidecars.
3. **Metadata registration**: processed recordings are registered in Postgres so experiments can query datasets, recordings, sampling rates, paths, labels, and processed variants.
4. **Benchmark execution**: the benchmark package creates deterministic subject-safe or recording-safe splits, builds reusable features, trains models, evaluates every source-trained model against every target dataset, and writes reproducible artifacts.
5. **Local observability**: the dashboard reads artifacts, logs, cache entries, configs, model metadata, and job progress so a researcher can inspect results without manually browsing S3 and Postgres.

The key design idea is separation of concerns:

- **The data pipeline does not decide experiment splits.** It makes recordings clean, traceable, and comparable.
- **The benchmark package does not know dataset-specific label quirks.** It consumes normalized annotations.
- **The dashboard does not own the benchmark logic.** It displays and queues the same CLI workflows used from the terminal.

This is better than a single notebook or one-off script because the framework keeps data provenance, label interpretation, split construction, model execution, and result artifacts explicit and reproducible.

## 2. Repository Map

The main repository areas are:

| Path | Role |
| --- | --- |
| `README.md` | Top-level quick start and repository overview. |
| `ecg_pipeline_specification_v4.md` | Current pipeline specification. v4 is the active design; v3 remains historical context. |
| `data-pipeline/` | Dataset manifests, downloader, readers, annotation adapters, processor, Airflow DAGs, Docker Compose services, and Postgres schema. |
| `py_ecg_benchmark_pkg/` | Benchmark Python package, CLI, config model, feature builder, split/cache logic, models, metrics, artifacts, and tests. |
| `dashboard/` | Local web dashboard for result inspection, config building, cache management, and experiment queueing. |
| `experiments/data_pipeline_test/` | Spark/Jupyter notebooks, demo logs, and dashboard job state used for local validation and exploration. |
| `scripts/` | Top-level scripts to start and stop the integrated local stack. |
| `docs/` | Draw.io schematic HTML files plus this deep-dive document. |

The practical entry points are:

- `./scripts/start_ecg_af_stack.sh`: starts Postgres, MinIO, pgAdmin, Spark/Jupyter, and optionally Airflow; also starts the dashboard.
- `uv run --project py_ecg_benchmark_pkg ecg-bench run --config ...`: runs a benchmark from YAML.
- `data-pipeline/scripts/run_dataset_pipeline.py`: runs dataset download, process, and register from a manifest passed with `--manifest`.
- Airflow DAGs in `data-pipeline/dags/`: orchestrate download, process/register, or full pipeline runs across manifests.

Supporting and historical assets:

- `data-pipeline/_eda/data_links.md` lists candidate open-access ECG datasets and sources.
- `data-pipeline/_eda/fetch_datasets.py` is a legacy hardcoded downloader/prototype. The production path is now manifest-driven.
- `experiments/DATASET_INFO_CLARIFICATION.md` and `data-pipeline/DATASET_INFO_CLARIFICATION.md` explain the project convention that one processed Parquet file usually represents one recording or slice, while annotations live in JSON sidecars.
- `docs/Pipeline Schematic.dc.html` and `docs/Data Pipeline Schematic.dc.html` are draw.io schematic exports that complement this text.
- `uv.lock` files in `data-pipeline/`, `py_ecg_benchmark_pkg/`, and `experiments/` pin Python dependency environments for their respective workspaces.

## 3. End-To-End Flow

The full framework flow is:

1. A dataset manifest in `data-pipeline/manifests/` defines source URL, provider, metadata, expected annotation adapter, and dataset-specific options.
2. The downloader resolves the provider strategy, downloads raw files, extracts archives safely, prunes unsupported files, and writes raw data into MinIO/S3 under `raw/<dataset>/`.
3. The processor loads raw keys, asks the dataset annotation adapter which files are sidecars, discovers signal files through format readers, reads signals into `ECGRecording` objects, enriches them with normalized annotations, optionally resamples to the configured rate, and writes:
   - `processed/<rate>hz/<dataset>/<recording>.parquet`
   - `processed/<rate>hz/<dataset>/<recording>.annotations.json`
   - a processing report JSON
4. The registrar upserts dataset and recording metadata into Postgres tables.
5. A benchmark config in `py_ecg_benchmark_pkg/examples/` selects source datasets, target datasets, windowing, label policy, split policy, models, metrics, storage, and Spark settings.
6. The benchmark workflow queries Postgres for processed recordings, creates deterministic safe splits, builds or reuses feature caches, trains source-specific models, evaluates each model against each target test split, and writes run artifacts under `benchmarks/runs/<run_id>/`.
7. The dashboard reads those artifacts and local job logs, producing result comparisons, timing charts, label distributions, run details, cache views, config builder output, and queue progress.

In presentation terms, the repository is not just a model benchmark. It is a reproducible chain from public ECG archives to cross-dataset model evaluation.

## 4. Local Runtime And Services

The local stack uses Docker Compose and environment files to make infrastructure reproducible on one machine.

Core services:

- **Postgres 16** stores dataset, recording, resampled recording, benchmark run, split, model, and metric metadata.
- **MinIO** provides an S3-compatible object store for raw data, processed data, benchmark artifacts, caches, and uploaded configs.
- **pgAdmin** is included for manual database inspection.
- **Airflow** is optional and runs the data-pipeline DAGs when started with the Airflow profile.
- **Spark/Jupyter** is started through `experiments/data_pipeline_test/docker-compose.spark.yaml` and is used for EDA and benchmark feature construction.
- **Dashboard** runs as a local Python HTTP server and talks to the same S3/Postgres/artifact layout as the CLI.

The top-level `scripts/start_ecg_af_stack.sh` is the preferred all-in-one starter. It creates missing env files from examples, syncs the benchmark package with `uv` unless `SKIP_UV_SYNC=1`, starts Docker services, exports host environment variables, and launches the dashboard with a Docker Compose runner backend. `scripts/stop_ecg_af_stack.sh` stops the dashboard and containers, with optional volume removal.

The data-pipeline-local helper `data-pipeline/scripts/dev_stack.sh` starts the data infrastructure plus Airflow from a Docker env file. It is narrower than the top-level script and is mainly useful when working directly on ingestion.

Docker support files:

- `data-pipeline/docker-compose.yaml` defines Postgres, MinIO, MinIO initialization, pgAdmin, and Airflow services.
- `data-pipeline/docker/postgres/init/01_airflow_db.sql` creates the Airflow database.
- `data-pipeline/docker/postgres/init/02_ecg_schema.sql` initializes the ECG data catalog schema.
- `data-pipeline/docker/airflow/Dockerfile` builds an Airflow image with the pipeline package installed editable.
- `data-pipeline/docker/airflow/airflow.cfg` holds Airflow configuration.
- `data-pipeline/docker/pgadmin/servers.json` preconfigures pgAdmin server entries.

Package and dependency boundaries:

- `data-pipeline/pyproject.toml` targets Python `>=3.11,<3.13` and depends on S3/Postgres/Parquet/numerical/ECG tooling: `boto3`, `botocore`, `psycopg2-binary`, `pyarrow`, `numpy`, `pandas`, `scipy`, `wfdb`, `h5py`, `requests`, and `pyyaml`. Its optional `airflow` extra adds Airflow and PySpark; its optional `benchmark` extra adds PySpark, scikit-learn, and torch for integration work.
- `py_ecg_benchmark_pkg/pyproject.toml` also targets Python `>=3.11,<3.13` and depends on the benchmark runtime stack: S3, Parquet, Postgres, PySpark, YAML, numpy/pandas, and scikit-learn. The `raw` extra adds torch for waveform models, and the `test` extra adds pytest.
- `experiments/pyproject.toml` is a separate Python `>=3.13` notebook workspace with Jupyter, Plotly, PySpark, torch, WFDB, and analysis libraries. Keeping it separate prevents exploratory notebook dependency choices from forcing the ingestion and benchmark packages onto the same Python/runtime schedule.

Why this design is good for this project:

- It avoids requiring cloud infrastructure for development.
- It keeps the object-storage API close to production-like S3.
- It lets benchmark artifacts be addressed with stable S3 URIs.
- It makes Postgres catalog state inspectable and queryable.
- It keeps Airflow optional, so the same core code can run from scripts, DAGs, notebooks, or tests.

Alternatives:

- A local filesystem could replace MinIO, but then the benchmark would lose S3 URI compatibility and object-style artifact organization.
- SQLite could replace Postgres for small demos, but it would be weaker for catalog joins, future multi-process access, and Airflow-style operation.
- Cloud S3/RDS could replace MinIO/Postgres, but local iteration would become slower, more expensive, and harder to reset.

## 5. Data Pipeline Design

The data pipeline is documented by `ecg_pipeline_specification_v4.md` and implemented under `data-pipeline/src/ecg_pipeline/`.

Its job is to turn heterogeneous ECG archives into a uniform, provenance-aware processed layer.

Important v4 design decisions:

- **Manifest-driven ingestion**: each dataset has a YAML manifest instead of hardcoded download and parsing logic.
- **Provider strategies**: PhysioNet S3, Zenodo, Figshare, and direct URL downloads are implemented behind a common interface.
- **Format readers are signal-first**: WFDB, CSV, MAT, and HDF5 readers extract signals and metadata, but do not interpret dataset-specific labels.
- **Annotation adapters own label semantics**: each dataset adapter reads sidecars and normalizes labels into canonical beat, rhythm, delineation, and label structures.
- **Splits are deferred**: ingestion does not assign train/validation/test splits. Benchmark configs create splits later.
- **AF presence is tri-state**: `TRUE` means AF annotated, `FALSE` means annotated but no AF, and `NULL` means unknown or not labelable.
- **Artifacts are idempotent**: download and processing stages write reports and can skip existing prefixes unless forced.

This is better than embedding label logic in readers because readers can be reused across datasets with the same file format but different annotation conventions. It is also better than putting splits into manifests because one processed dataset can support many experimental designs.

## 6. Data Pipeline Configuration

`data-pipeline/pipeline_config.yaml` defines the pipeline defaults:

- S3/MinIO bucket: `ecg-pipeline`
- processed sample rate: `250 Hz`
- normalization: `none`
- Parquet compression: `snappy`
- Spark memory-related defaults, although ingestion currently uses Python processing rather than a full Spark ingestion job
- Airflow retry behavior
- PhysioNet public bucket: `physionet-open`

`data-pipeline/src/ecg_pipeline/config.py` loads:

- pipeline YAML configuration
- `.env` files
- S3 endpoint/access key/secret
- Postgres connection settings
- Airflow-facing and script-facing defaults

Environment examples:

- `data-pipeline/.env.example` is host-oriented, using localhost services.
- `data-pipeline/.env.docker.example` is container-oriented, using Docker service names like `minio` and `postgres`.

This split matters because the same code runs from the host, Airflow containers, and Spark/Jupyter containers.

Shared pipeline utilities:

- `utils/env.py` implements a small `.env` parser instead of adding a separate runtime dependency.
- `utils/helpers.py` owns provider inference, URL parsing, slugification, and readable size formatting. It also explicitly rejects Harvard Dataverse portal URLs because the current provider strategy set no longer supports that path.
- `utils/s3.py` wraps boto3 with path-style S3 access, bucket creation, prefix listing, JSON/bytes upload, file upload/download, and prefix deletion. This keeps MinIO/AWS S3 mechanics out of the downloader, processor, and DAG code.

## 7. Dataset Manifests

Manifests are parsed by `data-pipeline/src/ecg_pipeline/manifest.py`.

A manifest describes:

- dataset name, slug, version, citation-like metadata, and subject/recording counts
- provider and source URL
- annotation adapter ID and options
- expected annotation types
- dataset-specific quirks

Supported providers are:

- `physionet_s3`
- `zenodo`
- `figshare`
- `direct_url`

The parser can infer providers from URLs and rejects old/deprecated fields such as `source.archive_format`. It ignores historical manifest splits while preserving extra fields for compatibility.

Current dataset manifests:

| Manifest | Provider | Adapter | Key role |
| --- | --- | --- | --- |
| `mit-bih-atrial-fibrillation-database.yaml` | PhysioNet S3 | `mit_bih_afdb_v1` | Classic AF rhythm annotations and corrected beat annotations. |
| `af-termination-challenge-database.yaml` | PhysioNet S3 | `aftdb_v1` | Synthetic full-record AF semantics for AF termination challenge records. |
| `cpsc-2021-paroxysmal-af-event-detection-dataset.yaml` | PhysioNet S3 | `cpsc2021_paroxysmal_af_v1` | Header class plus WFDB rhythm transitions for paroxysmal AF. |
| `arrhythmias-in-children-and-patients-with-congenital-heart-disease.yaml` | PhysioNet S3 | `leipzig_heart_center_ecg_v1` | Pediatric/congenital-heart-disease rhythms plus demographics. |
| `iridia-af-database.yaml` | Zenodo | `iridia_af_v1` | Long-term HDF5 ECG files with CSV labels spanning multiple files. |
| `ncku-cbic-ecg-database.yaml` | Figshare | `ncku_cbic_ecg_v1` | CSV signal and peak sidecars from a small wearable-style dataset. |

Adding a new dataset follows `data-pipeline/ADDING_DATASET.md`:

1. Add a manifest YAML.
2. Implement an annotation adapter.
3. Register/import the adapter.
4. Keep readers signal-only.
5. Validate the manifest and run the pipeline.

The current implementation is better than hardcoding datasets because every dataset-specific assumption lives in a small adapter plus a manifest. That makes reviews easier: one can ask "what exactly did we assume about this dataset's labels?" and answer by reading one adapter.

## 8. Download Layer

The downloader is implemented in `data-pipeline/src/ecg_pipeline/downloader/`.

`Downloader`:

- checks if the raw prefix already exists
- optionally deletes it when forced
- delegates to a provider strategy
- uploads files into S3/MinIO
- prunes unsupported file types
- writes `_download_report.json`

Provider strategies:

- **PhysioNet S3**: uses unsigned S3 access to `physionet-open`, parsing PhysioNet content/file URLs into bucket keys.
- **Zenodo**: calls the Zenodo records API, downloads listed files, extracts archives, and uploads extracted contents.
- **Figshare**: supports articles and collections, including article-title subfolders for collections.
- **Direct URL**: downloads a single URL and extracts if it is an archive.

Archive extraction is handled by `_extract.py`, which uses safe path joining to prevent path traversal. HTTP helpers are in `_http.py`.

The file filter currently retains common ECG data and metadata extensions: `.hea`, `.dat`, `.atr`, `.qrs`, `.csv`, `.mat`, `.h5`, `.hdf5`, `.md`, and `.txt`. Keeping `.md` and `.txt` is pragmatic because some datasets store useful metadata or sidecars there, even though earlier prose suggested pruning readmes more aggressively.

Alternatives:

- `wget`/shell scripts would be simpler initially but much harder to make provider-aware, idempotent, and reportable.
- Mirroring entire archives without filtering would preserve everything but increase storage and downstream noise.
- Dataset-specific download scripts would work for a few datasets but scale poorly as providers and archive structures differ.

## 9. Signal Readers

Signal reading lives in `data-pipeline/src/ecg_pipeline/reader/`.

The central model classes are:

- `ECGRecording`: signal matrix, sampling rate, channel names, units, subject metadata, source metadata, and annotations.
- `RecordingFileGroup`: the files that belong to one logical recording or slice.
- `ProcessingResult`: metadata for a successfully processed output.
- `ProcessingFailure`: structured failure information.

`FormatDispatcher` asks every registered reader to discover recordings from downloaded raw files and then dispatches each group to the reader that understands it.

Implemented readers:

| Reader | File families | Behavior |
| --- | --- | --- |
| `WFDBReader` | `.hea` plus WFDB companions such as `.dat`, `.atr`, `.qrs` | Uses `wfdb.rdrecord(physical=True)` to read physical signal values and channel names. |
| `CSVReader` | `.csv` | Detects numeric columns, optional time columns, sample rate hints from headers/sidecars/defaults, and returns a single signal matrix. |
| `MATReader` | `.mat` | Traverses nested MATLAB structures, scores signal-like arrays, infers sample rate from metadata/time arrays, and extracts channel hints where possible. |
| `HDF5Reader` | `.h5`, `.hdf5` | Traverses datasets, supports 1D/2D recordings and 3D batched tensors, with adapter-provided axis hints. |

Common helpers normalize array shape, sample-rate hints, channel names, and demographic values.

Why this is the right level of abstraction:

- Format handling is reusable across datasets.
- Dataset label conventions are not tied to file containers.
- New formats can be added without touching benchmark logic.
- New datasets can reuse existing readers and only add adapters.

Alternatives:

- A single general parser would become hard to reason about and hard to test.
- Dataset-specific readers would duplicate WFDB/CSV/MAT/HDF5 logic.
- Storing raw binary blobs only would delay format problems until benchmarking, where failures are more expensive.

## 10. Annotation Adapter Layer

Annotation handling lives in `data-pipeline/src/ecg_pipeline/annotations/`.

The base contract is `AnnotationAdapter`:

- `prepare(context)`: load global sidecars or metadata once.
- `sidecar_files(context)`: identify non-signal files that should not be dispatched to signal readers.
- `reader_options_for_group(group, context)`: provide dataset-specific reader hints, such as HDF5 axes.
- `enrich_recording(recording, group, context)`: attach normalized annotations and metadata.

Adapters can raise `AnnotationDrop` when a recording cannot be trusted because required annotations are missing. Drops are counted in processing reports.

`AnnotationContext` provides cached S3 access to bytes, text, CSV rows, and pattern-based file lookup. `registry.py` maps adapter IDs to classes.

Canonical annotation structures include:

- beat positions and beat types
- rhythm episodes with start/end samples and labels
- delineation landmarks
- generic labels and provenance metadata

Current adapters:

- **MIT-BIH AFDB**: reads WFDB rhythm annotations and corrected/automated beat annotations; normalizes rhythm notes such as AFIB/AF, AFL, NSR/N, and OTHER; handles known missing `.dat` records.
- **AFTDB**: requires `.qrs` beat files and treats records as full-record AF according to AF termination challenge semantics.
- **CPSC2021**: combines header-level class information with WFDB rhythm transitions; drops paroxysmal records when required transitions are missing.
- **Leipzig Heart Center ECG**: reads rhythm annotations and adult/children demographics sidecars; normalizes a broad rhythm vocabulary.
- **Iridia AF**: handles HDF5 ECG file slices, metadata CSV, calibration samples, and label CSV episodes that can span multiple files.
- **NCKU CBIC ECG**: reads CSV signal and peak/label sidecars, maps numeric rhythm codes to AF/AFL/OTHER, and reconstructs recording IDs from directory structure.

The adapter architecture is one of the strongest parts of the project. Public ECG datasets often look similar at the signal level but differ sharply in annotation semantics. Moving that logic into explicit adapters makes assumptions auditable.

## 11. Processing And Output Artifacts

Processing is centered in `data-pipeline/src/ecg_pipeline/reader/processor.py`.

For each dataset:

1. List raw S3 keys.
2. Build the annotation adapter from the manifest.
3. Let the adapter prepare any shared sidecar state.
4. Exclude sidecar files from signal discovery.
5. Discover signal groups through the format dispatcher.
6. Download each group to a temporary directory.
7. Read one or many `ECGRecording` objects.
8. Enrich recordings with annotations.
9. Resample signals and annotations if the target sample rate differs.
10. Write Parquet signal data and JSON annotations.
11. Write a processing report with results, failures, and dropped annotation counts.

`resampler.py` uses `scipy.signal.resample_poly` with a greatest-common-divisor up/down ratio. That is a good choice because it is less naive than index interpolation: it uses polyphase filtering and handles anti-aliasing. Annotation sample positions are rescaled consistently.

`writers.py` writes:

- one row per sample in Parquet
- `sample_index`
- channel columns such as `channel_0_II`
- metadata in the Parquet footer, including dataset, recording ID, sampling rates, channel names, duration, units, source format, original filename, and slice index
- annotation JSON sidecars with beats, rhythms, delineations, labels, and any adapter-provided provenance fields inside `labels`

Parquet plus JSON sidecars is better than one giant table for this framework because:

- waveform samples are large and columnar storage is efficient
- Spark can read Parquet directly
- channel columns can be selected cheaply
- annotations are irregular nested structures and fit JSON better
- processed signals remain usable even if a downstream experiment uses a different label policy

Alternatives:

- HDF5 could store signal and annotations together, but Spark interoperability and object-store partitioning would be weaker.
- A database table of all samples would be queryable but inefficient at ECG scale.
- Embedding annotations only in Parquet metadata would be awkward for long rhythm/beat lists.

Implementation reality:

- The v4 specification discusses PySpark for large-scale processing, and the config contains Spark-oriented fields. The current ingestion processor is a Python loop using pandas/pyarrow-style writes. Spark is used concretely in benchmark feature generation and notebooks. A future ingestion-scale Spark implementation could parallelize the current processing loop.

## 12. Postgres Catalog

The data catalog schema is in:

- `data-pipeline/src/ecg_pipeline/registrar/schema.sql`
- `data-pipeline/docker/postgres/init/02_ecg_schema.sql`

Main tables:

- `datasets`
- `recordings`
- `resampled_recordings`

Important fields include:

- dataset name/version/provider
- recording ID and subject ID
- sampling rate and duration
- channel names
- S3 paths for Parquet and annotations
- `af_present` tri-state
- nullable `split`

`MetadataRegistrar` upserts datasets and recordings, refreshes aggregate counts, and records resampled variants.

The nullable split field is intentional. The catalog can store historical or external split information, but the benchmark framework creates its own deterministic split manifests. This prevents one ingestion-time split from silently controlling every later experiment.

Alternatives:

- Keeping only JSON processing reports would be simpler, but every benchmark would have to rescan object storage.
- Putting all metadata into S3 object metadata would be brittle and hard to query.
- Storing split assignments only in Postgres would make reproducibility more dependent on mutable database state.

## 13. Airflow DAGs And Scripts

Airflow DAGs live in `data-pipeline/dags/`.

Implemented DAGs:

- `ecg_download_dataset`: `load_manifest -> download_extract_prune`
- `ecg_process_register_dataset`: `load_manifest -> process_dataset -> register_results`
- `ecg_dataset_pipeline`: full end-to-end download, process, and register

They support dynamic mapping across manifests, with `all_manifests=true` by default and optional single-dataset overrides.

Airflow params:

- `ecg_download_dataset`: `all_manifests`, `dataset_name`, `force_redownload`
- `ecg_process_register_dataset`: `all_manifests`, `dataset_name`, `target_rate`
- `ecg_dataset_pipeline`: `all_manifests`, `dataset_name`, `force_redownload`, `target_rate`

For all three DAGs, DAG run `conf` values override declared `params`. The `load_manifest` task returns manifest stems, and downstream tasks use Airflow dynamic task mapping over those dataset names.

Task return payloads are small dictionaries. Download tasks return dataset name and downloaded file count. Processing tasks return dataset name, ok/error recording counts, and processing report URI. Register tasks return dataset name, dataset ID, processed counts, registered count, and processing report URI.

Shared DAG helpers:

- load config and manifests
- parse Airflow params and DAG run config
- build S3 clients
- parse S3 URIs
- load successful processing results from reports for registration

CLI scripts mirror the DAG stages:

- `validate_manifest.py`
- `download_dataset.py`
- `process_dataset.py`
- `run_dataset_pipeline.py`
- `init_db.py`

This dual script/DAG design is valuable. Developers can reproduce a DAG task locally without Airflow, while Airflow can orchestrate the same implementation for longer runs.

## 14. Benchmark Package Goals

The benchmark package lives under `py_ecg_benchmark_pkg/src/ecg_benchmark/`.

Its central goal is cross-dataset evaluation:

- Train a fresh model for each configured source dataset.
- Evaluate each source-trained model against every configured target dataset.
- Report window-level metrics as primary.
- Report recording-level metrics as secondary, using configured aggregation.

This matrix is more informative than pooling all datasets into one train/test split. It exposes domain shift: a model can perform well when trained and tested on the same dataset family but poorly on a target dataset with different acquisition, demographics, lead placement, annotation conventions, or noise.

The package is installed and invoked through `uv` and the `ecg-bench` CLI.

## 15. Benchmark Configuration

`py_ecg_benchmark_pkg/src/ecg_benchmark/config.py` defines the config dataclasses:

- `BenchmarkConfig`
- `ExperimentInfo`
- `DataConfig`
- `SplitConfig`
- `StorageConfig`
- `ReuseConfig`
- `DatabaseConfig`
- `SparkConfig`
- `ModelConfig`

`random_seed` is required. The config can produce canonical JSON and a SHA-256 hash. That hash becomes part of run identity and cache reasoning.

Key config concepts:

- source datasets
- target datasets
- processed sample rate
- window seconds and stride seconds
- label policy
- lead policy and preferred leads
- recording aggregation policy
- recording label policy
- split ratios and stratification
- artifact bucket/prefix
- cache settings
- model list and parameters
- metrics list
- Spark resources
- database and S3 environment variable names

Example configs:

- `benchmark.yaml`: modest baseline benchmark over MIT-BIH AFDB and CPSC2021.
- `showcase_smoke.yaml`: integration-focused smoke run with several showcase models and small neural budgets.
- `showcase_full.yaml`: broader showcase with more datasets.
- `showcase_overnight.yaml`: longer run with 5-second stride and larger neural budgets.

This explicit config model is better than CLI flags alone because benchmark runs have many coupled choices. A YAML config can be saved, uploaded, hashed, previewed, and attached to artifacts.

## 16. Artifact Layout

Artifact paths are generated by `artifacts.py`.

Run artifacts are stored under:

`s3://<artifact_bucket>/<artifact_prefix>/<run_id>/`

Typical run files include:

- `config/resolved_config.json`
- `config/benchmark_config_reference.md`
- `splits/split_manifest.json`
- `features/feature_manifest.json`
- `features/<dataset>/recording_manifest.parquet`
- `features/<dataset>/windows.parquet`
- `features/<dataset>/waveform_windows.parquet`
- trained model files
- window prediction Parquet files
- recording prediction Parquet files
- `metrics/window_metrics.json` and `.csv`
- `metrics/recording_metrics.json` and `.csv`
- window-level compatibility aliases `metrics/metrics.json` and `.csv`
- `matrix/window_benchmark_matrix.csv`
- `matrix/recording_benchmark_matrix.csv`
- window-level compatibility alias `matrix/benchmark_matrix.csv`
- timing JSON/CSV
- `RUN_SUMMARY.md`
- progress JSONL when launched with a progress file

Cache artifacts live under:

- `benchmarks/cache/splits/<dataset>/<cache_key>/...`
- `benchmarks/cache/features/<dataset>/<cache_key>/...`

Cache keys include the inputs that should change split or feature content. For features, that includes source bucket/prefix, target rate, window/stride, label policy, lead policy, preferred leads, and a recording snapshot. Model IDs and metric choices are not part of feature cache keys because they do not change the feature rows.

This is better than run-only artifacts because many benchmark runs share expensive feature-building work. It is also better than a global mutable cache name because each cache key records the assumptions behind the files.

## 17. Splits

Split logic is in `splits.py`.

The framework supports deterministic grouping:

- prefer `subject:<dataset>:<subject_id>` when subject IDs exist
- fall back to recording-level grouping

This avoids leakage from the same subject or recording across train/validation/test where metadata allows it.

Stratification options include:

- none
- dataset
- `af_present`
- dataset plus `af_present`

Split manifests include counts and a split hash. They are written as artifacts and can be cached by dataset.

This is stronger than row-level or window-level splitting. Window-level splits can put adjacent windows from the same recording in both train and test, which inflates performance by leaking patient-specific and recording-specific structure.

## 18. Window Labeling

Window labeling is in `labels.py`.

The default policy is `af_vs_nsr`:

- positive windows are fully inside AF rhythm episodes
- negative windows are fully inside NSR rhythm episodes
- boundary-spanning, ambiguous, unmapped, AFL, OTHER, or UNKNOWN windows are dropped

Other policies support AF vs non-AF, AF-or-AFL vs NSR, and AF-or-AFL vs non-AF.

This conservative default is important. A 10-second window that straddles AF onset or includes ambiguous rhythm labels should not be forced into a clean class unless the experiment explicitly asks for that policy.

Alternatives:

- Majority-overlap labels would retain more windows but introduce noisier labels.
- Any-overlap positive labels would increase sensitivity to short AF episodes but could make positive windows mostly non-AF.
- Recording-level labels only would be simpler but would not train window-level detectors cleanly.

## 19. Feature Generation

Feature generation is implemented in `spark_features.py` and uses PySpark.

For each dataset, the feature builder:

1. Reads the Postgres recording catalog.
2. Loads annotation JSON sidecars.
3. Creates fixed windows according to config.
4. Labels windows using the benchmark label policy.
5. Reads signal Parquet from S3/MinIO through Spark.
6. Selects the preferred lead.
7. Computes signal features and RR-interval features.
8. Writes:
   - `recording_manifest.parquet`
   - `windows.parquet`
   - `waveform_windows.parquet`

`windows.parquet` contains tabular features. `waveform_windows.parquet` stores lazy waveform pointers rather than copying all samples into feature rows:

- source Parquet path
- lead column
- start sample
- end sample
- labels and metadata

This is a good compromise. Classical models get compact tabular rows, while raw waveform models can load waveform windows only when needed.

Why Spark:

- processed ECG is sample-level and can become large
- Parquet and S3A are Spark-native
- feature creation is dataset-level and parallelizable
- notebooks and benchmark feature building share the same data-access pattern

Alternatives:

- pandas would be simpler for small datasets but less robust as data grows.
- Precomputing all waveform tensors would make training simpler but storage-heavy.
- A database feature table would be queryable but less natural for large waveform-adjacent data.

## 20. Features

`features.py` provides lightweight feature utilities:

- lead selection by preferred list such as `II`, `MLII`, `I`, `V1`
- deterministic fallback lead selection
- signal statistics
- RR interval mean, standard deviation, coefficient of variation, and count

The feature set is intentionally simple. The framework is meant to compare models and datasets reproducibly, not to hide performance behind a large opaque feature engineering layer.

## 21. Model API

The model protocol is documented in `py_ecg_benchmark_pkg/docs/model_api.md` and implemented in `models/base.py`.

Each model exposes:

- `fit(rows)`
- `predict_proba(rows)`
- `save(uri)`
- `load(uri)`
- `ModelInfo`, including `model_id`, `input_kind`, `requires_training`, required columns, and configurable parameters

Input kinds:

- `tabular`: consumes feature rows from `windows.parquet`
- `waveform`: consumes waveform pointer rows and loads raw windows lazily

This protocol is intentionally small. It allows rule-based models, sklearn models, and PyTorch waveform models to fit into the same workflow without making the workflow depend on a single ML framework.

## 22. Implemented Models

Model registry and creation are in `models/baselines.py` and sibling modules.

Baseline and feature models:

| Model | Kind | Description |
| --- | --- | --- |
| `dummy_prior` | tabular | Predicts the training prevalence. |
| `rr_irregularity_rule` | tabular | Rule-based logistic score from RR irregularity; no training required. |
| `logistic_regression_features` | tabular | sklearn logistic regression over configured feature columns. |
| `random_forest_features` | tabular | sklearn random forest over configured feature columns. |
| `tateno_glass_rr_histogram` | tabular | Showcase approximation of RR irregularity thresholding. |
| `lake_moorman_cosen` | tabular | Showcase entropy-like RR irregularity model. |
| `petrenas_low_complexity_detector` | tabular | Low-complexity fusion of RR and heart-rate evidence. |
| `zabihi_rf_handcrafted` | tabular | Random forest over handcrafted features. |
| `bin_adaboost_tree_ensemble` | tabular | AdaBoost decision tree ensemble. |
| `goodfellow_xgboost_multifeature` | tabular | XGBoost if available, otherwise sklearn gradient boosting. |

Waveform showcase models:

| Model | Kind | Description |
| --- | --- | --- |
| `raw_cnn_1d` | waveform | Small 1D convolutional network over normalized raw windows. |
| `xiong_skip_cnn_1d` | waveform | Skip/residual 1D CNN style model. |
| `zihlmann_crnn_spectrogram` | waveform | Convolution plus bidirectional GRU style model. |
| `cpsc2021_resnet_transformer` | waveform | Residual CNN plus Transformer encoder style model. |

The showcase models are integration approximations rather than exact paper reproductions. That is an important caveat for presentations: they demonstrate the framework's ability to host different model families, not final clinical claims.

The PyTorch dependency is optional through the package's `raw` extra. This keeps the default install lighter for users who only run tabular baselines.

## 23. Training And Evaluation Workflow

The main workflow is in `workflow.py`.

`ecg-bench run` first loads and schema-validates the YAML with `load_config(..., validate=True)`. The separate `validate-config` subcommand can additionally check required environment variables and, with `--check-connections`, Postgres/MinIO availability.

The `run_full_workflow` sequence is:

1. emit `workflow_started` and a planned stage count when progress reporting is enabled
2. `make_splits`, which calls `initialize_run_artifacts`, ensures benchmark schema, writes resolved config/reference/summary artifacts, upserts the run, and creates or reuses split manifests
3. emit `run_resolved` once the run ID and artifact root are known
4. `build_features`
5. `train_all`
6. `evaluate_run`
7. `write_matrix`
8. emit `workflow_finished`, or `workflow_failed` on exception

Training:

- Each source dataset trains its own copy of every configured model.
- Split manifests choose train and validation rows.
- Model artifacts are saved under the run's model path.
- The benchmark catalog records model metadata and timings.

Evaluation:

- For each trained source/model pair, every configured target dataset is evaluated.
- Target test rows are selected by input kind.
- Optional `max_eval_windows` caps are deterministic and independent of source/model identity, so comparisons remain fair.
- Window predictions are written.
- Window metrics are computed.
- Recording predictions are aggregated.
- Recording metrics are computed.
- Metrics and timings are written to S3 and Postgres.

Matrix output:

- window-level benchmark matrix CSV
- recording-level benchmark matrix CSV
- legacy alias files for compatibility

The source-by-target matrix is the framework's core scientific product. It shows not just "which model scores highest," but also "which training dataset transfers to which target dataset."

## 24. Metrics And Recording Aggregation

Metrics are in `metrics.py`.

Supported metrics include:

- n
- positive count
- negative count
- AUROC
- average precision
- accuracy
- F1
- sensitivity/recall
- specificity
- precision
- negative predictive value
- balanced accuracy

Undefined metrics return `None` rather than fake values. For example, AUROC is undefined when the evaluated cohort has only one class.

Recording aggregation converts window scores into recording scores using policies such as:

- mean
- max
- majority vote

Recording labels can be derived with:

- any positive window
- majority
- fraction threshold

This dual reporting is useful because AF detection can be framed both as a window-level event-detection problem and a recording-level screening problem.

## 25. Benchmark Catalog

Benchmark catalog tables are defined in `catalog.py`.

They include:

- benchmark runs
- split records
- model records
- metric records

The benchmark catalog sits alongside the ingestion catalog. It does not replace artifact files; it indexes and summarizes them for querying.

Why both S3 artifacts and Postgres rows:

- S3 artifacts are immutable-ish, portable, and easy to inspect as files.
- Postgres rows support fast dashboard summaries and relational queries.
- If one layer is out of sync, the stored artifacts still contain the canonical run outputs.

## 26. CLI

The CLI is `ecg-bench`, implemented in `cli.py`.

Subcommands:

- `validate-config`
- `make-splits`
- `build-features`
- `train`
- `evaluate`
- `matrix`
- `dashboard`
- `run`

The dashboard subcommand forwards into the root `dashboard` package through `ecg_benchmark/dashboard.py`, which keeps the benchmark package interface convenient without moving all dashboard code into the package source tree.

Benchmark support modules:

- `artifacts.py` is the single source of truth for run IDs, S3 keys, cache keys, compatibility aliases, and artifact URIs.
- `cache.py` defines split and feature cache payloads and hashes.
- `storage.py` is the benchmark S3 wrapper and supports object listing, JSON/text upload, file upload/download, and prefix deletion.
- `runtime.py` detects or populates `JAVA_HOME` so PySpark can start reliably from CLI and dashboard-launched jobs.
- `progress.py` writes thread-safe JSONL events such as `plan`, `stage_started`, and `stage_finished`.
- `reporting.py` renders config references, run summaries, metric CSVs, and model reference metadata used by artifacts and the dashboard.

## 27. Dashboard

The dashboard lives in `dashboard/`.

It is a local-first web app built with:

- Python `ThreadingHTTPServer`
- static HTML/CSS/JavaScript
- the benchmark artifact store
- optional Postgres catalog access
- local dashboard job files

Main backend files:

- `server.py`: routes and HTTP handler.
- `services.py`: run summaries, cache management, explorer data, config validation, config saving/uploading.
- `results.py`: parses logs/job files, pivots metrics, attaches timings, derives label distributions and best metrics.
- `config_builder.py`: defaults, presets, YAML rendering, config validation, and command examples.
- `runner.py`: one-at-a-time experiment queue.
- `assets.py`: safe static/template file loading.
- `settings.py`: paths, defaults, artifact lists, metric priorities.

Main frontend files:

- `templates/index.html`
- `static/app.js`
- `static/styles.css`

Dashboard views:

- **Results**: run list, best metrics, metric comparison table, bar chart, time chart, target label distribution, run details, artifact table, delete results.
- **Explorer**: registered datasets and supported model metadata.
- **Config Builder**: presets, dataset/model/metric controls, validation, YAML preview, download, local save, S3 upload.
- **Run Experiment**: package/S3/upload config source, run command preview, queue, progress bar, stage list, stdout/stderr tails, cancel.
- **Cache**: reusable split and feature cache entries, filtered by dataset, with deletion.

The runner stores jobs under `dashboard_jobs` style directories with:

- `job.json`
- submitted config YAML
- `progress.jsonl`
- `stdout.log`
- `stderr.log`

It can run locally with the current Python executable or through `DockerComposeCommandFactory`, which maps host job paths into the Spark/Jupyter container and executes:

`python -m ecg_benchmark.cli run --config ... --progress-file ...`

This simple dashboard architecture is appropriate here. A heavier web framework would add dependencies and deployment decisions, while the core need is local inspection of artifacts and queue progress.

## 28. Technical Contracts And Schemas

This section is the "minute details" layer. A reader should be able to describe the framework's practical contracts without opening the source code.

### Manifest Contract

Dataset manifests are loaded into `DatasetManifest` and related dataclasses.

Required fields:

- `dataset.name`: stable dataset key used in raw/processed prefixes and Postgres.
- `dataset.display_name`: human-readable name.
- `source.url`: provider URL.
- `annotations.adapter`: adapter registry ID.

Optional or defaulted fields:

- `dataset.version`: defaults to `1.0.0`.
- `source.provider`: optional; inferred from URL when omitted.
- `force_redownload`: defaults to `false`; accepts booleans or string-like booleans such as `true`, `false`, `1`, `0`, `yes`, `no`.
- `metadata.description`, `citation`, `license`, `geographic_origin`, `num_subjects`, `num_recordings`, `leads`.
- `annotations.options`: adapter-specific mapping.
- `annotations.available`: boolean.
- `annotations.types`: list such as beat/rhythm/delineation.
- `quirks`: list of documented dataset caveats.
- any unknown top-level keys are preserved in `extra`.

Validation and compatibility details:

- `source.provider` must be one of `physionet_s3`, `zenodo`, `figshare`, or `direct_url`.
- `source.archive_format` is rejected; archive extraction is inferred from file extensions.
- `splits` is intentionally ignored if present because v4 defers splitting to benchmark configs.
- Harvard Dataverse portal URLs are rejected by provider inference because there is no active provider strategy for them.

### Download Contract

Each provider strategy yields `DownloadFileResult` rows:

- `status`: `ok`, `error`, or `skipped`
- `file`
- `message`
- `url`
- `s3_key`
- `size_bytes`

The downloader returns `DownloadResult`:

- `dataset_name`
- `s3_prefix`
- `file_count`
- `total_size_bytes`
- `download_timestamp`
- `skipped`
- `report_key`

The download report is written to:

`raw/<dataset>/_download_report.json`

Normal report fields:

- `generated_at_epoch`
- `dataset`
- `url`
- `provider`
- `results`
- `summary.ok_files`
- `summary.error_files`
- `summary.total_size_bytes`
- `summary.deleted_non_required_files`
- `summary.deleted_empty_folder_markers`

If raw files already exist and `force_redownload` is false, the report has `skipped: true`, a human-readable message, and a summary based on the pruning pass.

### Data-Pipeline In-Memory Contracts

Signal and annotation objects are deliberately small.

`RhythmEpisode`:

- `start_sample`
- `end_sample`
- `rhythm_type`
- `original_label`

`Delineation`:

- `p_onsets`, `p_offsets`
- `qrs_onsets`, `qrs_offsets`
- `t_onsets`, `t_offsets`

`ECGAnnotations`:

- `beat_positions`
- `beat_labels`
- `rhythm_episodes`
- `delineation`

`ECGRecording`:

- identity: `recording_id`, `recording_name`, `original_filename`
- signal: `signal`, `sampling_rate`, `num_channels`, `channel_names`, `duration_seconds`, `num_samples`, `units`
- provenance: `source_format`, `slice_index`
- demographics: `subject_id`, `age`, `sex`
- labels and annotations: `annotations`, `labels`

`RecordingFileGroup`:

- `recording_base_name`
- `primary_file`
- `associated_files`
- `detected_format`

`ProcessingResult`:

- identity/path fields: `recording_name`, `full_recording_id`, `parquet_path`, `annotations_path`
- signal fields: `sampling_rate`, `original_sampling_rate`, `num_channels`, `channel_names`, `duration_seconds`, `num_samples`, `source_format`, `slice_index`
- demographics: `subject_id`, `age`, `sex`
- annotation summary: `has_beat_annotations`, `has_rhythm_annotations`, `has_delineation_annotations`, `num_beat_annotations`, `num_rhythm_episodes`, `af_present`
- generic labels: `has_label_annotations`, `num_label_entries`

`ProcessingFailure`:

- `recording_name`
- `primary_file`
- `error`

### Processed Output Contract

Each processed ECG Parquet file has:

- `sample_index`
- one numeric column per channel, named `channel_<index>_<lead>` when a lead name is available, otherwise `channel_<index>`

Parquet footer metadata keys:

- `recording_id`
- `dataset_name`
- `sampling_rate`
- `original_sampling_rate`
- `num_channels`
- `channel_names`
- `duration_seconds`
- `units`
- `source_format`
- `original_filename`
- `slice_index`

Each annotation JSON sidecar contains:

- `recording_id`
- `slice_index`
- `annotation_source`
- `target_sampling_rate`
- `original_sampling_rate`
- `beat_annotations`: either `null` or `{positions, labels}`
- `rhythm_episodes`: list of `{start_sample, end_sample, rhythm_type, original_label}` style records
- `delineation`
- `labels`: adapter-specific metadata, provenance, and dataset label fields

Each processing report is written at:

`processed/<rate>hz/<dataset>/_processing_report.json`

Processing report fields:

- `generated_at_epoch`
- `dataset`
- `target_sampling_rate`
- `summary`: total, ok, dropped, missing-annotation, and error counts
- `dropped`: dropped counts and `by_reason`
- `results`: serialized `ProcessingResult` rows
- `failures`: serialized `ProcessingFailure` rows

### Feature Artifact Contract

`recording_manifest.parquet` contains one row per catalog recording considered by feature generation:

- `dataset_name`
- `full_recording_id`
- `recording_name`
- `parquet_path`
- `annotations_path`
- `duration_seconds`
- `sample_rate_hz`
- `window_seconds`
- `stride_seconds`
- `label_policy`
- `lead_policy`

`windows.parquet` contains labeled tabular feature windows:

- identity: `dataset_name`, `full_recording_id`, `recording_name`, `subject_id`
- window: `window_index`, `window_start_seconds`, `window_end_seconds`, `start_sample`, `end_sample`
- label: `label`, `rhythm`
- lead: `lead_column`, `lead_name`
- RR features: `rr_mean`, `rr_std`, `rr_cv`, `rr_count`
- signal features: `signal_mean`, `signal_std`, `signal_min`, `signal_max`, `signal_range`

`waveform_windows.parquet` contains lazy raw-window pointers:

- identity/window/label fields similar to `windows.parquet`
- `window_samples`
- `sample_rate_hz`
- `parquet_path`
- `annotations_path`
- `lead_column`
- `lead_name`

`FeatureBuildResult` rows used by workflow summaries contain:

- `dataset_name`
- `feature_uri`
- `waveform_uri`
- `source_recordings`
- `labeled_windows`
- `windows_uri`
- `recording_manifest_uri`
- `cache_key`
- `reused`

### Prediction, Metric, Matrix, And Timing Contracts

Window prediction Parquet rows contain:

- `source_dataset`
- `target_dataset`
- `model_id`
- `full_recording_id`
- `window_index`
- `window_start_seconds`
- `window_end_seconds`
- `label`
- `af_probability`

Recording prediction Parquet rows contain:

- `source_dataset`
- `target_dataset`
- `model_id`
- `full_recording_id`
- `label`
- `af_probability`

Metric rows are long-form:

- `run_id`
- `source_dataset`
- `target_dataset`
- `model_id`
- `evaluation_level`
- `metric_name`
- `metric_value`

Matrix CSV rows are wide-form:

- `source_dataset`
- `target_dataset`
- `model_id`
- one column per metric name present in the long-form rows

Timing rows contain:

- `run_id`
- `stage`
- `source_dataset`
- `target_dataset`
- `model_id`
- `input_kind`
- `started_at`
- `finished_at`
- `duration_seconds`
- optional `row_count`
- optional `validation_row_count`
- optional `requires_training`
- optional `wall_duration_seconds`
- optional `artifact_uri`

Progress JSONL events include:

- `workflow_started`
- `plan`
- `stage_started`
- `stage_finished`
- `run_resolved`
- `workflow_finished`
- `workflow_failed`

Stage IDs are generated as zero-padded counters plus stage names, such as `0001-splits` or `0007-evaluate`.

### Dashboard API Contract

The dashboard serves HTML and static assets:

- `GET /`
- `GET /index.html`
- `GET /static/<asset>`

Health, results, explorer, and cache:

- `GET /api/health`
- `GET /api/runs?limit=<n>`
- `GET /api/runs/<run_id>`
- `DELETE /api/runs/<run_id>`
- `GET /api/explorer`
- `GET /api/cache?dataset=<dataset-or-all>`
- `DELETE /api/cache` with `kind`, `dataset`, and `cache_key`

Config builder and config storage:

- `GET /api/config/options`
- `POST /api/config/validate`
- `POST /api/config/save`
- `POST /api/config/upload`

Runner:

- `GET /api/runner/options`
- `POST /api/runner/config`
- `GET /api/jobs`
- `POST /api/jobs`
- `GET /api/jobs/<job_id>`
- `POST /api/jobs/<job_id>/cancel`
- `DELETE /api/jobs/<job_id>`

The API is intentionally small and JSON-only. It is not an independent public API layer; it is the browser-facing adapter over the artifact store, config builder, and local runner.

### One Recording Through The Whole System

A single recording moves through the framework like this:

1. Its dataset manifest chooses a provider and adapter.
2. The downloader writes raw files under `raw/<dataset>/` and records a download report.
3. The annotation adapter marks sidecars so readers only see signal files.
4. The format dispatcher groups a primary signal file with associated files.
5. The reader creates an `ECGRecording` with signal matrix, channel names, sampling rate, and basic metadata.
6. The adapter enriches that recording with `ECGAnnotations`, subject/demographic fields, and adapter labels.
7. The processor resamples the signal and sample-index annotations when needed.
8. The writer emits Parquet samples and annotation JSON.
9. The registrar upserts a `recordings` row, including paths, durations, annotation counts, and `af_present`.
10. The benchmark catalog queries this row for a configured sample rate.
11. The split builder assigns the recording to train/val/test with its subject group.
12. The Spark feature builder turns rhythm episodes into labeled windows and writes tabular rows plus waveform pointers.
13. A model uses the source dataset's train windows.
14. Evaluation loads target test windows, produces window predictions, aggregates recording predictions, and writes metrics.
15. The dashboard reads the resulting artifact tree and shows the model/source/target behavior.

This is the framework's central invariant: raw dataset-specific chaos is progressively transformed into explicit, typed, reproducible contracts.

## 29. Experiments And Notebooks

`experiments/data_pipeline_test/` contains reproducibility and exploration assets.

Important files:

- `README.md`: explains the Spark/Jupyter environment.
- `Dockerfile.spark`: builds the notebook container with the benchmark package and optional raw dependencies.
- `docker-compose.spark.yaml`: connects Spark/Jupyter to MinIO and Postgres.
- `01_fetch_and_eda_processed_250hz.ipynb`: reads processed `250hz` Parquet keys from MinIO through Spark.
- `af_benchmark_framework_demo.ipynb`: demonstrates config loading, artifact layout, deterministic splits, window labels, metrics, baseline behavior, and a real processed-data smoke test.
- `dashboard_jobs/`: local dashboard job records from previous runs.
- `showcase-smoke-...log`: example run log.

The demo notebook's important methodological point is that row-order or window-level splits are misleading. It moves toward recording-disjoint splits and explains why small held-out recording counts can make recording-level metrics unstable.

## 30. Testing And Quality Coverage

The benchmark package has the strongest automated tests.

Covered areas include:

- config defaults, validation, hashes, and environment names
- artifact path generation
- cache key behavior
- deterministic splits
- label policies
- feature utilities
- metrics and aggregation
- baseline/model behavior
- workflow helper behavior
- dashboard run parsing, summaries, cache deletion, explorer metadata, config builder, and upload/save validation
- experiment runner queue behavior and Docker command mapping

The data-pipeline package currently does not appear to have a comparable pytest suite. That is a real risk area because adapters and readers encode many dataset-specific assumptions. The adapter design makes those assumptions localized, but automated tests with small fixture files would strengthen confidence.

Recommended future tests:

- manifest validation fixtures for each current dataset
- downloader strategy tests with mocked provider responses
- archive extraction safety tests
- reader tests for WFDB/CSV/MAT/HDF5 fixture files
- adapter tests for normalized rhythm/beat outputs
- processor tests for Parquet and annotation JSON output schema
- registrar tests against a temporary Postgres or compatible test database

## 31. Why The Current Architecture Is Strong

The current architecture is better than a notebook-only research workflow because:

- It separates ingestion, normalization, registration, benchmarking, and visualization.
- It records provenance at every layer.
- It keeps dataset-specific quirks explicit.
- It prevents accidental split leakage by deferring splits to benchmark configs.
- It supports reusable features and splits without hiding the cache key.
- It evaluates cross-dataset transfer rather than only pooled performance.
- It can run locally but uses S3/Postgres abstractions that map to production-like infrastructure.

The current architecture is better than a monolithic application because:

- Researchers can run only the stage they need.
- Airflow is optional rather than required.
- The CLI and dashboard share the same workflow implementation.
- New datasets mostly affect manifests and adapters.
- New models mostly affect the model registry and model class.

The current architecture is better than storing everything in Postgres because:

- ECG samples are large and columnar files are more appropriate.
- Object storage handles raw archives and artifact trees naturally.
- Postgres remains focused on metadata and queryable summaries.

The current architecture is better than storing everything as local files because:

- MinIO/S3 URIs are stable across scripts, notebooks, Spark jobs, and dashboard views.
- Artifacts can be listed, deleted by prefix, and addressed uniformly.
- The same code can move toward AWS S3 by changing endpoint/config.

## 32. Important Tradeoffs And Limitations

Known or important limitations:

- Ingestion processing is currently Python-loop based despite Spark-oriented config fields.
- Data-pipeline automated tests are limited or absent compared with the benchmark package.
- CSV/MAT/HDF5 readers necessarily use heuristics when source metadata is incomplete.
- Unit normalization is only as good as source metadata and adapter hints.
- `af_present` is useful but not a substitute for full rhythm-episode labels.
- AFTDB full-record AF labeling is a dataset-semantics assumption, not a universal rule.
- Showcase neural models are framework demonstrations, not validated reproductions of papers.
- Dashboard is local-first and not designed as a secure multi-user service.
- Cache correctness depends on cache key inputs staying aligned with all behavior that affects feature content.

These tradeoffs are acceptable for the current stage because the framework prioritizes reproducible research plumbing and cross-dataset comparability. The most valuable next hardening work is adapter/reader testing and scaling ingestion parallelism.

## 33. How To Present The Framework

A strong presentation can follow this sequence:

1. **Problem**: AF datasets differ in formats, labels, sampling rates, leads, demographics, and annotation quality. Comparing models fairly requires normalization and provenance.
2. **Ingestion solution**: manifests plus provider strategies download raw public datasets into MinIO.
3. **Normalization solution**: signal readers extract waveform data, annotation adapters normalize labels, and outputs become Parquet plus JSON.
4. **Catalog solution**: Postgres registers what exists and where it lives without freezing experimental splits.
5. **Benchmark solution**: YAML configs define reproducible source-target experiments with safe splits, feature caches, model APIs, and metrics.
6. **Scientific output**: the benchmark matrix shows cross-dataset generalization, not just within-dataset performance.
7. **Operations solution**: dashboard and CLI inspect the same artifacts and run the same workflow.
8. **Tradeoffs**: local-first, explicit, auditable, and extensible, with future work around data-pipeline tests and ingestion scaling.

Useful diagrams to show:

- Raw public archives -> MinIO raw -> processed Parquet/JSON -> Postgres catalog.
- Postgres catalog + processed S3 -> splits/features -> models -> predictions/metrics/matrix.
- Dashboard reading S3 artifacts, Postgres summaries, and local job logs.

## 34. Glossary

| Term | Meaning |
| --- | --- |
| Recording | A logical ECG record or slice produced by the processor. |
| Subject-safe split | A split where all recordings for a subject stay in one partition when subject IDs are known. |
| Recording-safe split | A fallback split where entire recordings stay in one partition. |
| Source dataset | Dataset used to train a model instance. |
| Target dataset | Dataset used to evaluate a trained source/model pair. |
| Window | Fixed-length segment of a recording, such as 10 seconds. |
| Window-level metric | Metric computed directly over labeled windows. |
| Recording-level metric | Metric computed after aggregating window scores by recording. |
| Annotation adapter | Dataset-specific parser that turns raw labels into canonical beat/rhythm structures. |
| Reader | Format-specific signal parser, such as WFDB, CSV, MAT, or HDF5. |
| Feature cache | Reusable dataset-level features keyed by data/window/label/lead assumptions. |
| Run artifact | Output tied to one benchmark config and run ID. |

## 35. Practical Commands

Start the full local stack:

```bash
./scripts/start_ecg_af_stack.sh
```

Start with Airflow:

```bash
WITH_AIRFLOW=1 ./scripts/start_ecg_af_stack.sh
```

Run a smoke benchmark:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench run \
  --config py_ecg_benchmark_pkg/examples/showcase_smoke.yaml
```

Validate a benchmark config:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench validate-config \
  --config py_ecg_benchmark_pkg/examples/benchmark.yaml
```

Run the dashboard directly through the benchmark CLI:

```bash
uv run --project py_ecg_benchmark_pkg ecg-bench dashboard
```

Validate a dataset manifest:

```bash
uv run --project data-pipeline python data-pipeline/scripts/validate_manifest.py \
  data-pipeline/manifests/mit-bih-atrial-fibrillation-database.yaml
```

Run one dataset pipeline from a manifest:

```bash
uv run --project data-pipeline python data-pipeline/scripts/run_dataset_pipeline.py \
  --manifest data-pipeline/manifests/mit-bih-atrial-fibrillation-database.yaml
```

## 36. Component Ownership Summary

| Component | Owns | Should not own |
| --- | --- | --- |
| Manifest | Dataset identity, source, adapter choice, documented quirks | Train/test splits, model settings |
| Downloader | Provider-specific raw acquisition | Signal parsing, label semantics |
| Reader | Format-specific signal extraction | Dataset-specific annotation interpretation |
| Adapter | Dataset label/demographic semantics | Generic file-format parsing |
| Processor | Normalized Parquet/JSON output | Benchmark split choices |
| Registrar | Queryable metadata | Large waveform storage |
| Benchmark config | Experiment design | Raw data download behavior |
| Split builder | Safe deterministic partitions | Annotation normalization |
| Feature builder | Window rows and waveform pointers | Model-specific training logic |
| Model API | Fit/predict/save/load | Artifact layout and orchestration |
| Workflow | Stage ordering and artifact writing | Dataset-specific parsing |
| Dashboard | Inspection, config construction, queueing | Scientific computation logic |

This ownership map is the easiest way to reason about future changes. If a change crosses several columns, it should be reviewed carefully because it may be mixing concerns that the framework deliberately separates.
