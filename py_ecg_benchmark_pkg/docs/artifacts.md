# Benchmark Artifacts, Cache, Timings, And Progress

This document describes what `ecg-bench` writes, what can be reused across runs,
and what the dashboard reads. It intentionally does not describe experiment
results.

## Run Root

Every run writes durable artifacts to MinIO/S3 under:

```text
s3://<artifact_bucket>/<artifact_prefix>/<run_id>/
```

With the default examples this is:

```text
s3://ecg-pipeline/benchmarks/runs/<run_id>/
```

`run_id` defaults to:

```text
<UTC timestamp>-<experiment name>-<resolved config hash prefix>
```

You can override it with `ecg-bench run --run-id <id>` or with the dashboard run
form.

## Artifact Layout

Expected run files:

```text
config/resolved_config.json
config/benchmark_config_reference.md
splits/split_manifest.json
features/feature_manifest.json
features/<dataset>/recording_manifest.parquet
features/<dataset>/windows.parquet
features/<dataset>/waveform_windows.parquet
models/<source_dataset>/<model_id>/
predictions/windows/<source_dataset>/<target_dataset>/<model_id>.parquet
predictions/recordings/<source_dataset>/<target_dataset>/<model_id>.parquet
metrics/window_metrics.json
metrics/window_metrics.csv
metrics/recording_metrics.json
metrics/recording_metrics.csv
metrics/metrics.json
metrics/metrics.csv
matrix/window_benchmark_matrix.csv
matrix/recording_benchmark_matrix.csv
matrix/benchmark_matrix.csv
timings/timings.json
timings/timings.csv
RUN_SUMMARY.md
```

Compatibility aliases:

- `metrics/metrics.json` and `metrics/metrics.csv` mirror window-level metrics.
- `matrix/benchmark_matrix.csv` mirrors the window-level matrix.

## Window And Recording Metrics

Window-level metrics are the primary benchmark. They use every labeled target
test window available to the model, unless the model config sets
`params.max_eval_windows` for a smoke run.

Recording-level metrics are secondary. They first aggregate window
probabilities per recording using `data.recording_aggregation`, then aggregate
truth labels using `data.recording_label_policy`, then compute the configured
metric list.

Supported score aggregation values:

- `mean`: average window probabilities.
- `max`: maximum window probability.
- `majority_vote`: fraction of windows whose probability is at least `0.5`.

Supported recording truth-label policies:

- `fraction_threshold`: positive when the fraction of AF windows is at least
  `data.recording_label_threshold`.
- `majority`: positive when at least half of evaluated windows are AF.
- `any`: positive when any evaluated window is AF.

The default metric list includes `n`, `positive`, and `negative` so label
composition is visible beside score metrics.

## Cross-Dataset Matrix

For each configured model, the benchmark trains one independent model instance
per source dataset. Each source-trained model is then evaluated against every
target dataset.

For example, with sources `A, B`, targets `A, B, C`, and models `m1, m2`, the
benchmark trains:

```text
A/m1, A/m2, B/m1, B/m2
```

and evaluates each trained artifact on:

```text
A, B, C
```

The matrix CSVs are wide convenience views over the stored metric rows. They do
not replace the long-form metrics CSVs.

## Dataset-Level Cache

Reusable cache entries live outside individual run roots:

```text
s3://<artifact_bucket>/<cache_prefix>/splits/<dataset>/<split_cache_key>/
s3://<artifact_bucket>/<cache_prefix>/features/<dataset>/<feature_cache_key>/
```

With defaults:

```text
s3://ecg-pipeline/benchmarks/cache/
```

Split cache entries contain:

```text
split_manifest.json
```

Feature cache entries contain:

```text
feature_manifest.json
recording_manifest.parquet
windows.parquet
waveform_windows.parquet
```

When cache reuse is enabled, each run still writes its own
`splits/split_manifest.json` and `features/feature_manifest.json`. Those run
manifests point to reused cache entries when compatible entries already exist.

## Cache Key Inputs

Split cache keys include:

- cache schema version
- dataset name
- `random_seed`
- split ratios
- `data.split.stratify_by`
- recording ids
- subject ids
- AF-present flags

Feature cache keys include:

- cache schema version
- dataset name
- source bucket
- processed prefix
- sample rate
- window length
- stride
- label policy
- lead policy
- preferred lead order
- recording ids
- subject ids
- AF-present flags
- sampling rates
- durations
- channel names
- processed parquet paths
- annotation sidecar paths

Model ids, model hyperparameters, target datasets, and metric lists do not enter
split or feature cache keys. That is deliberate: changing only the models or
metrics can reuse dataset partitions and generated windows.

## Cache Modes

`reuse.mode` controls cache behavior:

- `auto`: reuse compatible entries and build missing entries.
- `require`: reuse compatible entries and fail if any required entry is missing.
- `off`: do not use cache entries.

`reuse.splits` and `reuse.features` can independently disable split or feature
reuse while leaving the other enabled.

## Timings

Timing artifacts are written to:

```text
timings/timings.json
timings/timings.csv
```

Rows have these common fields:

- `run_id`
- `stage`
- `source_dataset`
- `target_dataset`
- `model_id`
- `input_kind`
- `started_at`
- `finished_at`
- `duration_seconds`
- `row_count`
- `artifact_uri`

Training rows also include:

- `validation_row_count`
- `requires_training`
- `wall_duration_seconds`

Timing stages:

- `train`: model fitting plus saving the model artifact.
- `inference`: `predict_proba` on one source/model/target test set.
- `evaluate`: inference plus writing predictions, aggregating recording scores,
  computing metrics, and writing evaluation artifacts.

For deterministic rule models with `requires_training: false`, benchmark
training duration is stored as `0`. Their actual fit/save wall time is preserved
in `wall_duration_seconds`.

The dashboard can plot a selected metric against evaluation/inference time or
against training plus evaluation time. For the second view, non-training models
therefore appear with only their evaluation cost.

## Progress JSONL

`ecg-bench run --progress-file <path>` writes structured progress events as JSON
lines. The dashboard uses this for live job status.

Event types:

- `workflow_started`
- `plan`
- `stage_started`
- `stage_finished`
- `run_resolved`
- `workflow_finished`
- `workflow_failed`

Stage names include:

- `split`
- `features`
- `train`
- `evaluate`
- `matrix`

The progress file is local to the machine or container running the command. It
is not a durable benchmark artifact unless a caller stores it separately.

## Cache Preview And Deletion In The Dashboard

The dashboard Cache tab lists entries under
`s3://<artifact_bucket>/<cache_prefix>/`, optionally filtered by dataset. It
shows the cache kind, dataset, key prefix, entry size/object count, manifest
settings, and source URI.

Deleting an entry deletes all objects under that cache-entry prefix. Deleting a
feature cache entry can make old run manifests that point at the entry unusable
for re-training or re-evaluation. It does not delete historical metrics,
matrices, model artifacts, or predictions already written under run roots.
