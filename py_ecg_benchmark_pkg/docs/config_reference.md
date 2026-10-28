# Benchmark Config Reference

Benchmark configs are YAML files consumed by `ecg-bench` subcommands that accept
`--config`, such as `validate-config`, `make-splits`, `build-features`, `train`,
`evaluate`, and `run`. The resolved config is written to:

`s3://<artifact_bucket>/<artifact_prefix>/<run_id>/config/resolved_config.json`

The examples folder intentionally contains only the essential configs:

- `examples/benchmark.yaml`: canonical tabular/RR baseline benchmark.
- `examples/showcase_smoke.yaml`: all showcase models on two datasets with fast
  neural-model caps.
- `examples/showcase_full.yaml`: all showcase models on every dataset described
  by `data-pipeline/manifests`.
- `examples/showcase_overnight.yaml`: all showcase models on every dataset with
  5-second stride and a larger CPU neural training budget.

For the exact artifact layout, reusable cache paths, timing rows, and progress
events, see `artifacts.md`.

Supported top-level keys are `random_seed`, `experiment`, `data`, `storage`,
`database`, `spark`, `reuse`, `models`, and `metrics`. Unknown top-level keys fail
validation.

## Required Fields

`random_seed` is required at the top level. It controls split generation and
model initialization for reproducible benchmark runs.

`data.source_datasets` must contain at least one Postgres catalog dataset name.
`data.target_datasets` defaults to `source_datasets` when omitted.

## Complete Field Reference

| Field | Type | Default | Supported values / notes |
| --- | --- | --- | --- |
| `random_seed` | non-negative integer | required | Controls split generation and model initialization. |
| `experiment.name` | string | `af-detection-benchmark` | Used in run metadata and generated run ids. |
| `experiment.description` | string or null | null | Human-readable run description. |
| `data.source_datasets` | list of strings | required | Train one model instance per source dataset. Must match Postgres dataset names. |
| `data.target_datasets` | list of strings | `source_datasets` | Evaluate every trained source model on every target dataset. |
| `data.sample_rate_hz` | positive integer | `250` | Processed sample-rate folder under `processed/<rate>hz/`. |
| `data.processed_prefix` | string | `processed` | Prefix for processed source data in `storage.source_bucket`. |
| `data.window_seconds` | positive integer | `10` | Fixed ECG window width. |
| `data.stride_seconds` | positive integer | `5` | Window step. Must be less than or equal to `window_seconds`. |
| `data.label_policy` | string | `af_vs_nsr` | Supported: `af_vs_nsr`, `af_vs_non_af`, `af_or_afl_vs_nsr`, `af_or_afl_vs_non_af`. |
| `data.lead_policy` | string | `single_preferred_lead` | Supported: `single_preferred_lead`. |
| `data.preferred_leads` | list of strings | `[II, MLII, I, V1]` | Lead priority for single-lead feature generation. The first matching processed ECG column wins. |
| `data.recording_aggregation` | string | `mean` | Supported: `mean`, `max`, `majority_vote`. |
| `data.recording_label_policy` | string | `fraction_threshold` | Supported: `fraction_threshold`, `majority`, `any`. |
| `data.recording_label_threshold` | number | `0.5` | Must be between `0` and `1`. Used by `fraction_threshold`. |
| `data.split.train` | number | `0.70` | Train ratio. Must be between `0` and `1`. |
| `data.split.val` | number | `0.15` | Validation ratio. Must be between `0` and `1`. |
| `data.split.test` | number | `0.15` | Test ratio. Must be between `0` and `1`. |
| `data.split.stratify_by` | string | `af_present` | Supported: `af_present`, `dataset`, `dataset_af_present`, `none`. |
| `storage.artifact_bucket` | string | `ecg-pipeline` | MinIO/S3 bucket for benchmark artifacts. |
| `storage.artifact_prefix` | string | `benchmarks/runs` | Prefix under `artifact_bucket` for run artifacts. |
| `storage.cache_prefix` | string | `benchmarks/cache` | Prefix under `artifact_bucket` for reusable split and feature cache entries. |
| `storage.source_bucket` | string | `artifact_bucket` | MinIO/S3 bucket for processed ECG data. |
| `storage.endpoint_env` | string | `S3_ENDPOINT_URL` | Environment variable name containing S3/MinIO endpoint. |
| `storage.access_key_env` | string | `S3_ACCESS_KEY` | Environment variable name containing S3/MinIO access key. |
| `storage.secret_key_env` | string | `S3_SECRET_KEY` | Environment variable name containing S3/MinIO secret key. |
| `database.host_env` | string | `POSTGRES_HOST` | Environment variable name containing Postgres host. |
| `database.port_env` | string | `POSTGRES_PORT` | Environment variable name containing Postgres port. |
| `database.db_env` | string | `POSTGRES_DB` | Environment variable name containing Postgres database name. |
| `database.user_env` | string | `POSTGRES_USER` | Environment variable name containing Postgres user. |
| `database.password_env` | string | `POSTGRES_PASSWORD` | Environment variable name containing Postgres password. |
| `spark.master_env` | string | `SPARK_MASTER` | Environment variable name containing Spark master, for example `local[*]`. |
| `spark.app_name` | string | `ecg-af-benchmark` | Spark application name. |
| `spark.driver_memory` | string or null | null | Optional Spark driver memory, for example `4g`. |
| `spark.executor_memory` | string or null | null | Optional Spark executor memory, for example `4g`. |
| `reuse.enabled` | boolean | `true` | Enables dataset-level reuse unless `reuse.mode` is `off`. |
| `reuse.splits` | boolean | `true` | Reuse compatible per-dataset train/val/test split assignments. |
| `reuse.features` | boolean | `true` | Reuse compatible per-dataset feature and waveform window parquet. |
| `reuse.mode` | string | `auto` | Supported: `auto`, `require`, `off`. `require` fails instead of rebuilding a missing cache entry. |
| `models` | list | default baseline models | Each item is either a model id string or `{id, params}` mapping. |
| `metrics` | list of strings | default metric list | Supported metric names are listed below. |

`experiment` may also be written as a plain string shorthand for
`experiment.name`.

## Workflow Semantics

`data.source_datasets` and `data.target_datasets` are not merged into one pooled
training dataset. The benchmark trains one independent model instance for every
source dataset and model id, then evaluates each trained artifact on every
target dataset. This produces per-source/per-target metric rows and matrix cells.

Window-level metrics are the primary benchmark output. Recording-level metrics
are derived from the same target window predictions using the configured
recording aggregation and label policy.

## Artifact Layout

All durable outputs are stored in MinIO/S3:

- `config/resolved_config.json`
- `config/benchmark_config_reference.md`
- `splits/split_manifest.json`
- `features/<dataset>/`
  - `recording_manifest.parquet`
  - `windows.parquet`
  - `waveform_windows.parquet`
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

Reusable dataset-level cache entries are stored outside individual run roots:

- `s3://<artifact_bucket>/<cache_prefix>/splits/<dataset>/<split_cache_key>/split_manifest.json`
- `s3://<artifact_bucket>/<cache_prefix>/features/<dataset>/<feature_cache_key>/`
  - `feature_manifest.json`
  - `recording_manifest.parquet`
  - `windows.parquet`
  - `waveform_windows.parquet`

No durable benchmark artifact should be written to local disk. Local files are
only acceptable as temporary working files before upload.

Metrics are written at two evaluation levels. Window-level metrics use every
labeled test window directly and are the primary benchmark output. Recording-
level metrics aggregate window probabilities per recording with
`data.recording_aggregation`, aggregate the truth label with
`data.recording_label_policy`, and compute the same metric list on the
aggregated recording predictions.

`data.recording_aggregation` controls scores:

- `mean`: average window probabilities.
- `max`: maximum window probability.
- `majority_vote`: fraction of windows whose probability is at least `0.5`.

`data.recording_label_policy` controls recording-level truth labels:

- `fraction_threshold`: positive when the fraction of AF windows is at least
  `data.recording_label_threshold`.
- `majority`: positive when at least half of evaluated windows are AF.
- `any`: positive when any evaluated window is AF. This reproduces the earlier
  behavior and can make long intermittent-AF recordings all positive.

The default metric list includes `n`, `positive`, and `negative` before the
score/classification metrics, so skewed or single-class evaluations are visible
in the exported CSVs.

## Label Policy

Window labels are assigned only when a window is fully contained inside accepted
rhythm episodes for the selected policy. Boundary-spanning, unknown, unlabeled,
and unmapped rhythms are dropped before feature/model training.

Supported `data.label_policy` values:

- `af_vs_nsr`: positive AF, negative NSR. AFL and OTHER are dropped.
- `af_vs_non_af`: positive AF, negative NSR/AFL/OTHER.
- `af_or_afl_vs_nsr`: positive AF/AFL, negative NSR. OTHER is dropped.
- `af_or_afl_vs_non_af`: positive AF/AFL, negative NSR/OTHER.

The pipeline annotation sidecar shape `start_sample`, `end_sample`, and
`rhythm_type` is supported directly; second-based episode fields are also
accepted.

## Split Policy

Splits are benchmark artifacts, not ingestion metadata. With reuse enabled, split
assignments are generated and cached per dataset, then composed into each run's
`splits/split_manifest.json`. Adding dataset C to a later config can therefore
reuse compatible cached splits for datasets A and B.

The split generator groups by `subject_id` when available and falls back to
recording id otherwise. This prevents subject leakage when the catalog has
subject metadata.

`data.split.stratify_by` is configurable:

- `af_present`: keep AF-present and AF-absent groups balanced across splits.
- `dataset`: split independently per dataset.
- `dataset_af_present`: split independently per dataset and AF-present stratum.
- `none`: no stratification beyond the subject/recording grouping.

For cross-dataset configs, `dataset_af_present` is usually the safest default
because it keeps each dataset represented in each split when enough recordings
exist.

Split cache keys include dataset name, recording ids, subject ids, AF-present
flags, split ratios, stratification, and `random_seed`. Feature cache keys
include dataset name, processed data paths, sampling rate, window/stride,
label/lead policy, preferred-lead order, and the recording snapshot. Model ids
and metric lists do not enter either cache key, so model-only experiment changes
reuse both partitions and generated windows.

## Models

Default model ids:

- `dummy_prior`
- `rr_irregularity_rule`
- `logistic_regression_features`
- `random_forest_features`

All supported model ids:

- `dummy_prior`
- `rr_irregularity_rule`
- `logistic_regression_features`
- `random_forest_features`
- `raw_cnn_1d`
- `tateno_glass_rr_histogram`
- `lake_moorman_cosen`
- `petrenas_low_complexity_detector`
- `zabihi_rf_handcrafted`
- `bin_adaboost_tree_ensemble`
- `goodfellow_xgboost_multifeature`
- `xiong_skip_cnn_1d`
- `zihlmann_crnn_spectrogram`
- `cpsc2021_resnet_transformer`

Model entries can be written as a plain id:

```yaml
models:
  - dummy_prior
```

or as an id plus model-specific params:

```yaml
models:
  - id: random_forest_features
    params:
      n_estimators: 200
      class_weight: balanced
```

Model-specific params are documented in `docs/model_api.md` and in the generated
run config reference snapshot.

## Model Params

`random_state` is added automatically from `random_seed` for models that declare
it. You can still override it explicitly in a model's `params`.

DSP/RR-style params:

| Model id | Param | Type | Default | Supported values / notes |
| --- | --- | --- | --- | --- |
| `rr_irregularity_rule` | `threshold` | number | `0.12` | RR-CV value mapped to probability `0.5`. |
| `rr_irregularity_rule` | `slope` | number | `12.0` | Logistic slope around `threshold`. |
| `tateno_glass_rr_histogram` | `threshold` | number or omitted | fitted or `0.18` | RR-CV threshold. If omitted and both classes exist, it is fitted from training data. |
| `tateno_glass_rr_histogram` | `slope` | number | `18.0` | Logistic slope around `threshold`. |
| `lake_moorman_cosen` | `threshold` | number or omitted | fitted or `0.35` | Entropy-proxy threshold. If omitted and both classes exist, it is fitted from training data. |
| `lake_moorman_cosen` | `slope` | number | `10.0` | Logistic slope around `threshold`. |
| `petrenas_low_complexity_detector` | `irregularity_threshold` | number or omitted | fitted or `0.12` | RR-CV irregularity threshold. |
| `petrenas_low_complexity_detector` | `rate_threshold_bpm` | number | `80.0` | Heart-rate evidence threshold. |
| `petrenas_low_complexity_detector` | `slope` | number | `14.0` | Logistic evidence slope. |
| `petrenas_low_complexity_detector` | `rate_weight` | number | `0.25` | Weight for heart-rate evidence. Values are clipped to `[0, 1]`. |

Tabular feature-model params:

| Model id | Param | Type | Default | Supported values / notes |
| --- | --- | --- | --- | --- |
| `logistic_regression_features` | `feature_columns` | list of strings or omitted | numeric non-identity columns | Recommended explicit list is shown below. |
| `logistic_regression_features` | `max_iter` | positive integer | `1000` | Passed to sklearn `LogisticRegression`. |
| `logistic_regression_features` | `class_weight` | string, mapping, or null | `balanced` | Passed to sklearn. |
| `random_forest_features` | `feature_columns` | list of strings or omitted | numeric non-identity columns | Recommended explicit list is shown below. |
| `random_forest_features` | `n_estimators` | positive integer | `200` | Number of trees. |
| `random_forest_features` | `max_depth` | integer or null | null | Maximum tree depth. |
| `random_forest_features` | `class_weight` | string, mapping, or null | `balanced` | Passed to sklearn. |
| `zabihi_rf_handcrafted` | `feature_columns` | list of strings or omitted | numeric non-identity columns | Recommended explicit list is shown below. |
| `zabihi_rf_handcrafted` | `n_estimators` | positive integer | `250` | Number of trees. |
| `zabihi_rf_handcrafted` | `max_depth` | integer or null | null | Maximum tree depth. |
| `zabihi_rf_handcrafted` | `max_features` | string, number, or null | `sqrt` | Passed to sklearn `RandomForestClassifier`. |
| `zabihi_rf_handcrafted` | `class_weight` | string, mapping, or null | `balanced` | Passed to sklearn. |
| `bin_adaboost_tree_ensemble` | `feature_columns` | list of strings or omitted | numeric non-identity columns | Recommended explicit list is shown below. |
| `bin_adaboost_tree_ensemble` | `n_estimators` | positive integer | `80` | Boosting rounds. |
| `bin_adaboost_tree_ensemble` | `learning_rate` | positive number | `0.5` | AdaBoost learning rate. |
| `bin_adaboost_tree_ensemble` | `max_depth` | positive integer | `2` | Weak decision-tree depth. |
| `goodfellow_xgboost_multifeature` | `feature_columns` | list of strings or omitted | numeric non-identity columns | Recommended explicit list is shown below. |
| `goodfellow_xgboost_multifeature` | `backend` | string | `sklearn` | Supported: `sklearn`, `xgboost`. `xgboost` requires installing xgboost separately. |
| `goodfellow_xgboost_multifeature` | `n_estimators` | positive integer | `150` | Boosting rounds. |
| `goodfellow_xgboost_multifeature` | `learning_rate` | positive number | `0.05` | Boosting learning rate. |
| `goodfellow_xgboost_multifeature` | `max_depth` | positive integer | `3` | Tree depth. |

## Model Feature Columns

The current tabular baselines and feature-ML showcase models should usually be
configured with explicit `params.feature_columns`. The examples use RR summary
features plus simple Spark-generated signal summaries:

- `rr_count`
- `rr_mean`
- `rr_std`
- `rr_cv`
- `signal_mean`
- `signal_std`
- `signal_min`
- `signal_max`
- `signal_range`

This keeps model training off positional metadata such as `window_index`,
`start_sample`, and `end_sample`.

## Raw-Waveform Models

Raw-waveform models use `features/<dataset>/waveform_windows.parquet` and lazy
ECG loading from MinIO/S3. Install the package with the `raw` extra to make
PyTorch available:

```bash
python -m pip install -e ".[raw]"
```

Supported raw-waveform model ids:

- `raw_cnn_1d`
- `xiong_skip_cnn_1d`
- `zihlmann_crnn_spectrogram`
- `cpsc2021_resnet_transformer`

Common raw-waveform params:

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `target_length` | positive integer or null | inferred | Samples per window after resampling/pad/truncate. |
| `epochs` | positive integer | `3` | Training epochs. |
| `batch_size` | positive integer | `32` | Training batch size. |
| `learning_rate` | positive number | `0.001` | Adam learning rate. |
| `max_train_windows` | positive integer or null | `2048` | Training-window cap for smoke runs. |
| `max_eval_windows` | positive integer or null | null | Optional evaluation-window cap for fast smoke runs. Metrics are computed on the capped deterministic subset. |
| `device` | string | `auto` | `auto`, `cpu`, `cuda`, or another PyTorch device string. |
| `random_state` | integer | benchmark seed | Added automatically by the CLI when supported. |

## Metrics

Default metrics (reported in every run unless overridden):

| Metric | Description |
| --- | --- |
| `n` | Total number of evaluated windows. |
| `positive` | Number of AF-labelled windows. |
| `negative` | Number of NSR-labelled windows. |
| `auroc` | Area under the ROC curve. Threshold-independent ranking metric. Undefined when only one class is present. |
| `average_precision` | Area under the precision–recall curve (AP). More informative than AUROC on imbalanced sets. Undefined when there are no positive windows. |
| `accuracy` | Fraction of correctly classified windows `(TP + TN) / n`. Not sufficient as the primary metric for imbalanced rhythm detection. |
| `f1` | Harmonic mean of precision and sensitivity: `2TP / (2TP + FP + FN)`. Undefined when `TP + FP + FN = 0`. |
| `sensitivity` | True positive rate: `TP / (TP + FN)`. Undefined when there are no positive windows. |
| `specificity` | True negative rate: `TN / (TN + FP)`. Undefined when there are no negative windows. |

Additional supported metrics (opt-in via `metrics` list):

| Metric | Description |
| --- | --- |
| `precision` | Positive predictive value: `TP / (TP + FP)`. Undefined when there are no positive predictions. |
| `recall` | Alias for `sensitivity`: `TP / (TP + FN)`. Provided for compatibility with conventions that use recall rather than sensitivity. |
| `npv` | Negative predictive value: `TN / (TN + FN)`. Undefined when there are no negative predictions. |
| `balanced_accuracy` | Average of sensitivity and specificity: `(sensitivity + specificity) / 2`. Robust to class imbalance. Undefined when either class is absent. |

All metrics that cannot be computed for a given evaluation (e.g. AUROC when a target test set contains
only one class) are recorded explicitly as `null` rather than as a misleading numeric value.

## Environment Variables

The default config expects:

- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY`
- `S3_SECRET_KEY`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `SPARK_MASTER` for Spark when present
