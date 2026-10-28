# ECG AF Benchmark Package

`py_ecg_benchmark_pkg` implements the AF detection benchmark framework from
`PLAN.md` as a reusable Python package plus the `ecg-bench` CLI.

The package assumes the data pipeline has already written processed ECG parquet
and annotation sidecars to MinIO/S3 under:

```text
s3://ecg-pipeline/processed/250hz/<dataset_name>/
```

Benchmark artifacts are written under:

```text
s3://ecg-pipeline/benchmarks/runs/<run_id>/
```

For the storage contract, reusable cache, timing rows, and dashboard progress
events, see `docs/artifacts.md`.

## How The Benchmark Works

The benchmark is cross-dataset by construction. For every configured source
dataset, it trains a fresh instance of every configured model. Each trained
source/model artifact is then evaluated against every configured target dataset.

Window-level metrics are the primary benchmark output. Recording-level metrics
are also written by aggregating the window scores and labels for each recording
with the policies configured under `data.recording_aggregation` and
`data.recording_label_policy`.

Models consume one of two generated input tables:

- `tabular`: RR summaries and simple signal statistics from
  `features/<dataset>/windows.parquet`.
- `waveform`: raw single-lead window manifests from
  `features/<dataset>/waveform_windows.parquet`, loaded lazily from MinIO/S3.

Dataset-level split and feature caches are independent of model ids and metric
lists. Re-running with new models can reuse existing compatible partitions and
windows.

## What Is Implemented

- Documented YAML config loading, defaults, validation, and config hashing.
- Canonical run artifact path layout.
- Deterministic subject-safe or recording-safe split generation.
- AF-vs-NSR window labeling from rhythm annotations.
- Simple signal and RR-interval feature helpers.
- Window-level primary metrics plus configurable recording-level aggregation.
- Dataset-level split and feature/window cache reuse.
- Timing artifacts for model training, inference, and evaluation stages.
- Baseline model API and baseline models:
  - `dummy_prior`
  - `rr_irregularity_rule`
  - `logistic_regression_features`
  - `random_forest_features`
  - `raw_cnn_1d` optional PyTorch raw-waveform baseline
- Paper-inspired showcase models:
  - DSP/RR: [`tateno_glass_rr_histogram`](https://pubmed.ncbi.nlm.nih.gov/11804173/), [`lake_moorman_cosen`](https://pubmed.ncbi.nlm.nih.gov/21037227/), [`petrenas_low_complexity_detector`](https://pubmed.ncbi.nlm.nih.gov/25666902/)
  - Feature ML: [`zabihi_rf_handcrafted`](https://physionet.org/files/challenge-2017/1.0.0/papers/069-336.pdf), [`bin_adaboost_tree_ensemble`](https://www.cinc.org/archives/2017/pdf/342-204.pdf), [`goodfellow_xgboost_multifeature`](https://www.cinc.org/archives/2017/pdf/361-352.pdf)
  - Raw-waveform NN: [`xiong_skip_cnn_1d`](https://www.cinc.org/archives/2017/pdf/066-138.pdf), [`zihlmann_crnn_spectrogram`](https://www.cinc.org/archives/2017/pdf/070-060.pdf), [`cpsc2021_resnet_transformer`](https://pmc.ncbi.nlm.nih.gov/articles/PMC10220587/)
- Postgres benchmark metadata tables and adapter.
- MinIO/S3 artifact adapter.
- PySpark feature-building entry point.
- CLI workflow for `validate-config`, `make-splits`, `build-features`, `train`,
  `evaluate`, `matrix`, and `run`.
- Local dashboard for result browsing, metric/time plots, cache preview/deletion,
  config building, and queued benchmark runs.

## Install And Import

For local development with a Python 3.11 or 3.12 environment:

```bash
cd py_ecg_benchmark_pkg
python -m pip install -e ".[test]"
```

For the optional raw-waveform models, install the `raw` extra as well:

```bash
python -m pip install -e ".[test,raw]"
```

Without installing, run from the repository root with:

```bash
PYTHONPATH=py_ecg_benchmark_pkg/src python -m ecg_benchmark.cli --help
```

Inside the Spark EDA container, `experiments/data_pipeline_test/docker-compose.spark.yaml`
builds an image with `py_ecg_benchmark_pkg[raw]` installed, mounts this package
at `/home/jovyan/py_ecg_benchmark_pkg`, and sets:

```text
PYTHONPATH=/home/jovyan/py_ecg_benchmark_pkg/src:/usr/local/spark/python:/usr/local/spark/python/lib/py4j-0.10.9.7-src.zip
```

That makes both `import ecg_benchmark` and the `ecg-bench` console script
available in the notebook/CLI smoke-test container.

## Configuration

Start from:

```text
py_ecg_benchmark_pkg/examples/benchmark.yaml
```

Required:

- `random_seed`: top-level integer used for deterministic splits and model seeds.
- `data.source_datasets`: dataset names as registered in Postgres.

Important defaults:

- `data.window_seconds: 10`
- `data.stride_seconds: 5`
- `data.label_policy: af_vs_nsr` (`af_vs_non_af`, `af_or_afl_vs_nsr`, and `af_or_afl_vs_non_af` are also supported)
- `data.lead_policy: single_preferred_lead`
- `data.recording_aggregation: mean`
- `data.recording_label_policy: fraction_threshold`
- `data.recording_label_threshold: 0.5`
- `data.split.stratify_by: af_present`
- `storage.artifact_bucket: ecg-pipeline`
- `storage.artifact_prefix: benchmarks/runs`
- `storage.cache_prefix: benchmarks/cache`
- `reuse.enabled: true`
- `reuse.mode: auto`

More detail:

- `docs/config_reference.md`
- `docs/model_api.md`
- `docs/artifacts.md`
- `../dashboard/README.md`

The examples folder is intentionally small:

- `examples/benchmark.yaml`: canonical tabular/RR baseline benchmark.
- `examples/showcase_smoke.yaml`: all paper-inspired showcase models on AFTDB
  and MIT-BIH AFDB with strict neural training and evaluation caps.
- `examples/showcase_full.yaml`: all showcase models on all datasets currently
  described by `data-pipeline/manifests`.
- `examples/showcase_overnight.yaml`: same nine showcase models as
  `showcase_full.yaml`, but with denser 5-second stride and a larger CPU neural
  training budget.

The example config pins sklearn baseline `feature_columns` to RR summaries plus
simple signal summaries: `rr_count`, `rr_mean`, `rr_std`, `rr_cv`,
`signal_mean`, `signal_std`, `signal_min`, `signal_max`, and `signal_range`.
This avoids training on window-position metadata such as `window_index`,
`start_sample`, and `end_sample`.

Raw-waveform models read `features/<dataset>/waveform_windows.parquet` and
require installing the package with the `raw` extra.

Split assignments and generated feature/waveform windows are cached per dataset
by default. Model-only or metric-only config changes reuse compatible cached
partitions; changing `random_seed` invalidates split cache entries, while
changing window length, stride, label policy, lead policy, or processed data
invalidates feature cache entries.

## Environment

Spark feature/window generation requires a Java runtime. On the host, install
Java and set `JAVA_HOME` if your distribution does not expose it automatically.
The `spark-eda` Docker service already includes Java.

Default environment variables:

```bash
JAVA_HOME=/path/to/java/home              # optional if auto-detected
S3_ENDPOINT_URL=http://localhost:9000      # host scripts
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=ecg_metadata
POSTGRES_USER=ecg_pipeline
POSTGRES_PASSWORD=...
SPARK_MASTER=local[*]
```

Inside Docker Compose notebooks, `S3_ENDPOINT_URL=http://minio:9000` is correct.
From host-side scripts, use `http://localhost:9000`.

## CLI Usage

The CLI reads connection settings from environment variables; it does not load
`.env` files itself. From the repository root on the host, export the
data-pipeline env file before running live commands:

```bash
test -f data-pipeline/.env || cp data-pipeline/.env.example data-pipeline/.env
set -a
source data-pipeline/.env
set +a
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1"
export no_proxy="${no_proxy:+$no_proxy,}localhost,127.0.0.1"
```

Inside the Spark notebook container, these variables are already provided by
Docker Compose.

The examples below assume you then run:

```bash
cd py_ecg_benchmark_pkg
CONFIG=examples/benchmark.yaml
```

The commands below use `uv run` so they work from a fresh checkout. If you
installed the package with `pip install -e ...` or are inside the Spark
container, you can omit `uv run`. All commands accept `--config "$CONFIG"`
unless noted.

```bash
uv run ecg-bench validate-config \
  --config "$CONFIG" \
  --skip-env
```

Validate config, defaults, model ids, metric names, and optionally live
Postgres/MinIO connectivity:

```bash
uv run ecg-bench validate-config --config "$CONFIG" --check-connections
```

Create deterministic split manifests and write them to MinIO/S3 and Postgres:

```bash
uv run ecg-bench make-splits --config "$CONFIG"
```

Build feature parquet with Spark:

```bash
uv run ecg-bench build-features --config "$CONFIG" --run-id <run_id>
```

Train one model on one source dataset:

```bash
uv run ecg-bench train \
  --config "$CONFIG" \
  --run-id <run_id> \
  --source-dataset mit-bih-atrial-fibrillation-database \
  --model rr_irregularity_rule
```

Evaluate trained models on configured target datasets:

```bash
uv run ecg-bench evaluate --config "$CONFIG" --run-id <run_id>
```

Write the final metrics matrix:

```bash
uv run ecg-bench matrix --run-id <run_id>
```

This writes separate matrices for window-level and recording-level evaluation.
`matrix/benchmark_matrix.csv` is the window-level compatibility alias because
window-level metrics are the primary benchmark result.

Run the full workflow:

```bash
uv run ecg-bench run --config "$CONFIG"
```

Emit structured progress JSONL for dashboard-style monitoring:

```bash
uv run ecg-bench run \
  --config "$CONFIG" \
  --progress-file /tmp/ecg-benchmark-progress.jsonl
```

`build-features` and `run` require Spark. The recommended local environment is
the Spark notebook container described below; a host environment also works if
Java, PySpark, and the configured S3A dependencies are available.

## Results Dashboard And Config Builder

The full dashboard guide is in `../dashboard/README.md`.

Serve the local dashboard:

```bash
uv run ecg-bench dashboard
```

Then open:

```text
http://127.0.0.1:8765
```

The `Results` tab reads the existing MinIO/S3 artifact layout under
`s3://ecg-pipeline/benchmarks/runs/`. It lists runs, shows when each run
started/finished when local logs are available, summarizes elapsed time, and
compares window-level and recording-level metrics by model/source/target pair.

Dashboard implementation files live in the repository-root `dashboard/` folder:
server routes in `server.py`, data aggregation in `services.py`, config-builder
logic in `config_builder.py`, page markup in `templates/`, and browser assets
in `static/`.

The `Config Builder` tab creates benchmark YAML from presets, validates it with
the same `BenchmarkConfig` rules used by the CLI, previews the resolved YAML and
config hash, and can either download the YAML file locally from the browser or
save normalized YAML to the configured MinIO/S3 config prefix.

The `Run Experiment` tab can queue benchmark runs from package examples, MinIO/S3
config files, or local YAML uploads. The dashboard runs one experiment
subprocess at a time, records progress events as JSONL, and shows a live stage
list for split creation/reuse, feature window creation/reuse, model training,
evaluation, and matrix writing. Queued jobs can be removed before they start.
The Cache tab previews dataset-level split/window cache entries and can delete
stale entries by dataset/key.

If host-side Java/Spark is not available, run queued experiments inside the
Spark notebook container:

```bash
uv run ecg-bench dashboard \
  --runner-backend docker-compose \
  --runner-job-dir ../experiments/data_pipeline_test/dashboard_jobs
```

With this mode, the dashboard stays on the host but launches benchmark jobs via
`docker compose exec -T spark-eda ...`. The job directory must be mounted into
the container; the default data-pipeline test stack mounts
`experiments/data_pipeline_test` to `/home/jovyan/work`.

Downloaded configs include command snippets for:

```bash
uv run ecg-bench validate-config --config <downloaded_config>
uv run ecg-bench run --config <downloaded_config>
```

Useful options:

```bash
uv run ecg-bench dashboard \
  --artifact-bucket ecg-pipeline \
  --artifact-prefix benchmarks/runs \
  --cache-prefix benchmarks/cache \
  --s3-config-prefix benchmarks/configs \
  --log-dir ../experiments/data_pipeline_test
```

If Postgres is unavailable, dataset selection falls back to manual text entry and
schema-only validation still works. Enable `Check connections` in the builder
when you want Postgres dataset and S3 artifact-bucket validation as well.

## Artifact Layout

This section is a quick index. The detailed storage, cache, timing, and progress
reference is `docs/artifacts.md`.

For a run id `<run_id>`, outputs are under:

```text
s3://<artifact_bucket>/<artifact_prefix>/<run_id>/
```

Expected artifacts:

- `config/resolved_config.json`
- `config/benchmark_config_reference.md`
- `splits/split_manifest.json`
- `features/<dataset>/recording_manifest.parquet`
- `features/<dataset>/windows.parquet`
- `features/<dataset>/waveform_windows.parquet`
- `models/<source_dataset>/<model_id>/`
- `predictions/windows/<source_dataset>/<target_dataset>/<model_id>.parquet`
- `predictions/recordings/<source_dataset>/<target_dataset>/<model_id>.parquet`
- `metrics/window_metrics.json`
- `metrics/window_metrics.csv`
- `metrics/recording_metrics.json`
- `metrics/recording_metrics.csv`
- `metrics/metrics.json` window-level compatibility alias
- `metrics/metrics.csv` window-level compatibility alias
- `matrix/window_benchmark_matrix.csv`
- `matrix/recording_benchmark_matrix.csv`
- `matrix/benchmark_matrix.csv` window-level compatibility alias
- `timings/timings.json`
- `timings/timings.csv`
- `RUN_SUMMARY.md`

Reusable cache entries live outside individual runs:

- `s3://<artifact_bucket>/<cache_prefix>/splits/<dataset>/<split_cache_key>/`
- `s3://<artifact_bucket>/<cache_prefix>/features/<dataset>/<feature_cache_key>/`

Each run still writes its own split and feature manifests. When cache reuse is
active, those manifests record which dataset partitions were reused and where
the cached parquet files live.

Evaluation stores both granularities:

- window-level metrics use every labeled test window directly and are the main
  benchmark output;
- recording-level metrics first aggregate window probabilities per recording
  with `data.recording_aggregation`, aggregate truth labels with
  `data.recording_label_policy`, then compute the same configured metrics.

The default metric list starts with `n`, `positive`, and `negative`, then reports
AUROC, average precision, accuracy, F1, sensitivity, and specificity. This makes
label composition visible beside the performance scores, which matters for
intermittent AF recordings and small smoke tests.

Recording-level truth labels are configurable:

- `fraction_threshold`: positive when the fraction of AF windows is at least
  `data.recording_label_threshold` (`0.5` by default).
- `majority`: positive when at least half of evaluated windows are AF.
- `any`: positive when any evaluated window is AF, matching the original
  behavior.

Split stratification is also configurable with `data.split.stratify_by`:
`af_present`, `dataset`, `dataset_af_present`, or `none`. With reuse enabled,
splits are generated per dataset and composed into the run-level manifest, so
adding a new dataset can reuse compatible cached partitions for the existing
datasets.

## Notebook Demo

The practical real-data smoke test lives at:

```text
experiments/data_pipeline_test/af_benchmark_framework_demo.ipynb
```

Run it from the Spark notebook container:

```bash
test -f data-pipeline/.env.docker || cp data-pipeline/.env.docker.example data-pipeline/.env.docker

docker compose \
  --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f experiments/data_pipeline_test/docker-compose.spark.yaml \
  up -d postgres minio minio-init spark-eda
```

Open:

```text
http://localhost:8889/lab?token=ecg
```

The default notebook token is `ecg`; override it by setting `JUPYTER_TOKEN`
before starting the compose stack.

The demo:

- imports `ecg_benchmark` from the mounted package source;
- lists processed MinIO data;
- reads ECG parquet and annotation JSON with Spark;
- builds a small RR-interval feature sample from real annotations;
- uses a recording-disjoint split;
- reports window-level and recording-level baseline metrics.

## Public API Reference

### `ecg_benchmark.config`

- `ConfigError`: validation exception with user-facing `errors`.
- `SplitConfig`: train/val/test ratio and stratification settings.
- `DataConfig`: dataset names, sample rate, windowing, label policy, lead policy,
  recording score aggregation, and recording truth-label aggregation settings.
- `StorageConfig`: artifact/source bucket and prefix settings.
- `DatabaseConfig`: Postgres environment variable names.
- `SparkConfig`: Spark app and resource settings.
- `ModelConfig`: one configured model id plus params.
- `ExperimentInfo`: experiment name and description.
- `BenchmarkConfig`: resolved full config; exposes `to_dict()`,
  `canonical_json()`, `config_hash()`, `required_env_vars()`,
  `missing_env_vars()`, and `processed_dataset_uri(dataset_name)`.
- `load_config(path, validate=True)`: read YAML and return `BenchmarkConfig`.
- `validate_env(config, env=None)`: ensure required env vars are present.

### `ecg_benchmark.artifacts`

- `ArtifactLayout`: generates all canonical S3 keys/URIs for one run.
- `ParsedS3Uri`: parsed bucket/key pair.
- `parse_s3_uri(uri)`: validate and split an `s3://bucket/key` URI.
- `sanitize_run_id(value)`: make run ids path-safe.
- `make_run_id(config, now=None)`: timestamp/name/hash run id.

### `ecg_benchmark.splits`

- `RecordingRecord`: catalog-shaped input row for split generation.
- `SplitAssignment`: output split assignment.
- `make_deterministic_splits(records, seed, train_ratio, val_ratio, test_ratio, stratify_by)`: deterministic subject-safe split; falls back to recording id when `subject_id` is absent. Supported stratification values are `af_present`, `dataset`, `dataset_af_present`, and `none`.
- `split_manifest(assignments, seed, config_hash, ratios)`: JSON-ready split artifact.
- `split_hash(payload)`: stable SHA-256 hash for split manifests.

### `ecg_benchmark.labels`

- `RhythmEpisode`: rhythm interval in seconds.
- `WindowLabel`: label assigned to one fixed-size window.
- `canonical_rhythm(label)`: normalize source labels to `AF`, `NSR`, or drop.
- `label_window(episodes, window_start, window_end, policy)`: label one window.
- `iter_window_bounds(duration_seconds, window_seconds, stride_seconds)`: yield fixed window bounds.
- `labels_for_recording(...)`: label all candidate windows in one recording.
- `episodes_from_annotation_json(payload, sample_rate_hz=None)`: parse pipeline annotation sidecars with `start_sample`, `end_sample`, and `rhythm_type`.

### `ecg_benchmark.features`

- `LeadSelection`: selected lead column metadata.
- `select_single_lead(columns, preferred_leads)`: choose a preferred ECG lead with deterministic fallback.
- `numeric_window_features(samples, rr_intervals_seconds=None)`: simple signal and RR feature dictionary.
- `rr_interval_features(rr_intervals_seconds)`: RR mean/std/CV/count features.

### `ecg_benchmark.metrics`

- `BinaryPrediction`: one binary label/score pair, optionally tied to a recording.
- `compute_binary_metrics(predictions, threshold=0.5, metric_names=None)`: count, prevalence, AUROC, average precision, accuracy, F1, sensitivity, specificity, and related metrics with `None` for undefined cases.
- `aggregate_by_recording(predictions, policy='mean', label_policy='any', label_threshold=0.5)`: aggregate window scores and truth labels to recording predictions.

### `ecg_benchmark.models`

- `ModelInfo`: model metadata for docs and registry.
- `BenchmarkModel`: protocol for model extensions:
  - `fit(train_features, val_features, config)`
  - `predict_proba(features)`
  - `save(s3_uri)`
  - `load(s3_uri)`
- `ModelNotFittedError`: raised when prediction/save needs fitted state.
- `create_model(model_id)`: instantiate a registered model.
- `load_model(model_id, s3_uri)`: load a registered model artifact.
- `model_class_registry()`: mapping from model id to model class.
- `model_infos()`: metadata for all registered models.
- `info_as_dict(info)`: serialize `ModelInfo`.

Baseline classes:

- `DummyPriorModel`: constant training prevalence baseline.
- `RRIrregularityRuleModel`: logistic rule over `rr_cv`.
- `LogisticRegressionFeaturesModel`: scikit-learn logistic regression.
- `RandomForestFeaturesModel`: scikit-learn random forest.
- `RawCnn1DModel`: optional PyTorch 1D CNN over raw single-lead waveform windows.

Paper-inspired showcase classes:

- `TatenoGlassRRHistogramModel`: RR-CV histogram-inspired DSP detector from
  [Tateno and Glass](https://pubmed.ncbi.nlm.nih.gov/11804173/).
- `LakeMoormanCosenModel`: short-window RR entropy-inspired detector from
  [Lake and Moorman](https://pubmed.ncbi.nlm.nih.gov/21037227/).
- `PetrenasLowComplexityModel`: low-complexity RR irregularity/rate fusion
  detector from [Petrenas, Marozas, and Sornmo](https://pubmed.ncbi.nlm.nih.gov/25666902/).
- `ZabihiRandomForestModel`: handcrafted-feature random forest showcase model
  inspired by the [Zabihi et al. CinC 2017 entry](https://physionet.org/files/challenge-2017/1.0.0/papers/069-336.pdf).
- `BinAdaBoostTreeModel`: compact AdaBoost tree ensemble showcase model from
  the [Bin et al. CinC 2017 entry](https://www.cinc.org/archives/2017/pdf/342-204.pdf).
- `GoodfellowGradientBoostingModel`: boosted multidisciplinary-feature showcase
  model from the [Goodfellow et al. CinC 2017 entry](https://www.cinc.org/archives/2017/pdf/361-352.pdf).
- `XiongSkipCnn1DModel`: skip-connected raw-waveform 1D CNN inspired by
  [Xiong et al.](https://www.cinc.org/archives/2017/pdf/066-138.pdf).
- `ZihlmannCrnnSpectrogramModel`: compact convolutional recurrent waveform
  model inspired by [Zihlmann, Tschannen, and Perekrestenko](https://www.cinc.org/archives/2017/pdf/070-060.pdf).
- `Cpsc2021ResNetTransformerModel`: residual CNN plus Transformer waveform
  model inspired by [CPSC2021 paroxysmal-AF event detection work](https://pmc.ncbi.nlm.nih.gov/articles/PMC10220587/)
  and the [CPSC2021 PhysioNet resource](https://physionet.org/content/cpsc2021/).

### `ecg_benchmark.storage`

- `S3ArtifactStore`: boto3 wrapper for MinIO/S3.
  - `from_env()`
  - `ensure_bucket(bucket)`
  - `prefix_exists(bucket, prefix)`
  - `iter_keys(bucket, prefix)`
  - `put_bytes(bucket, key, data, ...)`
  - `put_json(bucket, key, payload, ...)`
  - `put_text(bucket, key, text, ...)`
  - `get_bytes(bucket, key)`
  - `upload_file(bucket, key, filename)`
  - `download_file(bucket, key, filename)`

### `ecg_benchmark.catalog`

- `CatalogRecording`: Postgres recording row adapted for benchmark use.
- `BenchmarkCatalog`: Postgres benchmark adapter.
  - `from_env(database=None)`
  - `close()`
  - `ensure_schema()`
  - `dataset_names()`
  - `recordings_for_datasets(dataset_names, sample_rate_hz=None)`
  - `upsert_run(...)`
  - `insert_split_assignments(run_id, assignments)`
  - `upsert_model(...)`
  - `upsert_metrics(..., evaluation_level='recording')`
  - `get_run(run_id)`

### `ecg_benchmark.spark_features`

- `FeatureBuildResult`: dataset feature-build summary.
- `build_features_with_spark(config, layout, recordings)`: read processed parquet and annotation sidecars, build labeled tabular window features plus raw waveform window manifests, and write feature artifacts to MinIO/S3.

### `ecg_benchmark.reporting`

- `config_reference_markdown()`: generate a config reference snapshot.
- `run_summary_markdown(...)`: generate run summary Markdown.
- `metrics_to_csv(rows)`: serialize metric rows as CSV text.
- `model_reference()`: return model metadata as dictionaries.

### `ecg_benchmark.workflow`

- `validate_config_file(path, check_env=True, check_connections=False)`: high-level config validation.
- `initialize_run_artifacts(config, run_id=None)`: write resolved config/reference/summary artifacts.
- `make_splits(config, run_id=None)`: create split manifest and persist it.
- `build_features(config, run_id)`: build feature artifacts for configured datasets.
- `train_model(config, run_id, source_dataset, model_id)`: train and save one model.
- `train_all(config, run_id)`: train all configured source/model combinations.
- `evaluate_run(config, run_id)`: write predictions and metrics for configured targets.
- `write_matrix(run_id, config=None)`: pivot stored window and recording
  metrics to separate matrix CSVs.
- `run_full_workflow(config, run_id=None)`: run split, feature, train, evaluate, and matrix steps.
- `load_metrics_payload(path)`: read local metrics JSON.

### `ecg_benchmark.cli`

- `build_parser()`: construct the CLI parser.
- `main(argv=None)`: CLI entry point used by the `ecg-bench` console script.

## Development Checks

Syntax check:

```bash
python -X pycache_prefix=/tmp/ecg_bench_pycache -m compileall -q \
  py_ecg_benchmark_pkg/src py_ecg_benchmark_pkg/tests
```

Unit tests when `pytest` is installed:

```bash
cd py_ecg_benchmark_pkg
python -m pytest -q
```

Minimal config smoke test from repo root:

```bash
PYTHONPATH=py_ecg_benchmark_pkg/src python -m ecg_benchmark.cli validate-config \
  --config py_ecg_benchmark_pkg/examples/benchmark.yaml \
  --skip-env
```
