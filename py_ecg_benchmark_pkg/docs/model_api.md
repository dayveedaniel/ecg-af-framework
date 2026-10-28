# Benchmark Model API

Every benchmark model implements:

```python
fit(train_features, val_features, config)
predict_proba(features)
save(s3_uri)
load(s3_uri)
```

`predict_proba` returns AF probabilities, where higher scores mean stronger
evidence for atrial fibrillation. Model artifacts are saved under:

`s3://<artifact_bucket>/<artifact_prefix>/<run_id>/models/<source_dataset>/<model_id>/`

The artifact, timing, and progress contract around model training/evaluation is
documented in `artifacts.md`.

Models declare an `input_kind` in their `ModelInfo`:

- `tabular`: train/evaluate rows are read from `features/<dataset>/windows.parquet`.
- `waveform`: train/evaluate rows are read from `features/<dataset>/waveform_windows.parquet`.

Models also declare `requires_training`. Use `False` for deterministic rule or
adapter models whose `fit` method only applies configuration and persists a
loadable artifact. Benchmark timing artifacts record those models with
`train.duration_seconds = 0`, so they still appear on training-plus-evaluation
dashboard plots.

## Adding A Model

Add one module under `src/ecg_benchmark/models/`, implement the `BenchmarkModel`
protocol, and register the class in the model registry. Prefer one public class
per paper/competition idea so configs can refer to stable model ids.

Use an adapter when an original implementation exists. The adapter should:

- translate benchmark rows into the input shape expected by the original model;
- keep source/target dataset names and benchmark labels outside the model logic;
- save enough state for `load(s3_uri)` to reproduce `predict_proba`;
- expose original hyperparameters through `ModelInfo.configurable_parameters`;
- keep large temporary files local and persist durable model artifacts to
  MinIO/S3.

Use a reproduction module when no public code is available. In that case, keep
the implementation intentionally modest and document the paper link and the
parts that are approximated. The showcase models in this package follow that
pattern: they demonstrate integration with the benchmark rather than claiming
exact paper reproduction.

Choose `input_kind='tabular'` for RR/statistical-feature methods and
`input_kind='waveform'` for models that need raw ECG samples. Raw models receive
manifest rows containing `parquet_path`, `lead_column`, `start_sample`, and
`end_sample`; they should load the waveform lazily instead of storing all samples
in the config or model artifact.

## Required Metadata

Each model declares:

- model id
- required input columns
- output score semantics
- configurable parameters
- reproducibility behavior
- whether benchmark training time should be counted

## Baseline Models

`dummy_prior`

Constant baseline that predicts the AF prevalence observed in the training
split. Required column: `label`.

`rr_irregularity_rule`

Rule baseline that maps `rr_cv` to AF probability with a logistic transform.
Required column: `rr_cv`.

`logistic_regression_features`

Scikit-learn logistic regression over numeric feature columns. Required columns:
`label` plus numeric feature columns.

`random_forest_features`

Scikit-learn random forest over numeric feature columns. Required columns:
`label` plus numeric feature columns.

`raw_cnn_1d`

Optional PyTorch 1D CNN over raw single-lead waveform windows. Required columns:
`label`, `parquet_path`, `lead_column`, `start_sample`, and `end_sample`.
The model loads ECG samples lazily from MinIO/S3 using the waveform window
manifest. Install with `.[raw]` to make PyTorch available.

## Showcase Models

The package also includes paper-inspired showcase models. These are intended to
demonstrate how external research ideas can be expressed through the benchmark
API; they are not exact reproductions of the original competition submissions.

DSP/RR-style models:

- `tateno_glass_rr_histogram`: RR coefficient-of-variation evidence inspired by
  [Tateno and Glass RR/delta-RR histogram AF detection](https://pubmed.ncbi.nlm.nih.gov/11804173/).
- `lake_moorman_cosen`: short-window RR entropy proxy inspired by Lake and
  [Moorman's coefficient of sample entropy work](https://pubmed.ncbi.nlm.nih.gov/21037227/).
- `petrenas_low_complexity_detector`: low-complexity RR irregularity plus
  heart-rate fusion inspired by
  [Petrenas, Marozas, and Sornmo](https://pubmed.ncbi.nlm.nih.gov/25666902/).

Feature-ML models:

- `zabihi_rf_handcrafted`: random forest over explicit tabular ECG/RR features,
  inspired by the tied first-place
  [PhysioNet/CinC 2017 Zabihi entry](https://physionet.org/files/challenge-2017/1.0.0/papers/069-336.pdf).
- `bin_adaboost_tree_ensemble`: compact AdaBoost tree ensemble inspired by the
  [CinC 2017 decision-tree ensemble entry](https://www.cinc.org/archives/2017/pdf/342-204.pdf).
- `goodfellow_xgboost_multifeature`: boosted-tree classifier inspired by
  [multidisciplinary feature/XGBoost work](https://www.cinc.org/archives/2017/pdf/361-352.pdf);
  uses sklearn by default and can use XGBoost when installed.

Raw-waveform neural models:

- `xiong_skip_cnn_1d`: skip-connected 1D CNN over waveform windows inspired by
  [Xiong et al.'s CinC 2017 neural-network entry](https://www.cinc.org/archives/2017/pdf/066-138.pdf).
- `zihlmann_crnn_spectrogram`: compact convolutional recurrent network. The
  showcase implementation uses waveform convolutions plus GRU pooling, inspired
  by [Zihlmann, Tschannen, and Perekrestenko](https://www.cinc.org/archives/2017/pdf/070-060.pdf).
- `cpsc2021_resnet_transformer`: residual CNN plus Transformer encoder inspired
  by [CPSC2021 paroxysmal-AF event detection work](https://pmc.ncbi.nlm.nih.gov/articles/PMC10220587/)
  and the [CPSC2021 PhysioNet resource](https://physionet.org/content/cpsc2021/).

Example configs:

- `examples/benchmark.yaml`: canonical tabular/RR baseline benchmark.
- `examples/showcase_smoke.yaml`: all showcase models, two datasets, strict
  neural training and evaluation caps for a quick demonstration.
- `examples/showcase_full.yaml`: all showcase models and all datasets described
  by the data-pipeline manifests.
- `examples/showcase_overnight.yaml`: all showcase models with 5-second stride
  and a larger CPU neural training budget.

Use `max_train_windows` to cap raw-waveform training rows and
`max_eval_windows` to cap evaluation rows for smoke/demo runs. Full benchmark
configs should usually leave `max_eval_windows` unset so metrics use all target
test windows.

The recommended explicit tabular feature set for the current Spark feature
builder is:

- `rr_count`
- `rr_mean`
- `rr_std`
- `rr_cv`
- `signal_mean`
- `signal_std`
- `signal_min`
- `signal_max`
- `signal_range`

Set this list with `params.feature_columns` in the benchmark YAML. If omitted,
the sklearn wrapper falls back to numeric feature columns and excludes label,
recording identity, split, subject, and window-position metadata.
