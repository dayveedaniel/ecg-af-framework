# Dataset Info Clarification

This note clarifies what one processed Parquet file represents in this pipeline.

## Unit of Data

Exactly: one Parquet file is typically one processed recording (sometimes a slice/segment), not one table row.

## What Each Parquet File Contains

- Many rows, where each row is one time sample.
- Columns such as:
  - `sample_index`
  - `channel_0_*`, `channel_1_*`, etc. (ECG lead values)

Example:
- At `250 Hz`, a 10-second recording has about `2500` rows in that one Parquet file.

## About "User" vs Recording

- It is not always `1 user = 1 file`.
- A subject can have multiple recordings, and each recording can become its own Parquet file.
- In some datasets, "recording" is the reliable unit, not explicit user identity.

## Where Metadata and Labels Live

- Recording-level info (`recording_id`, dataset name, sampling rate, original sampling rate, etc.) is stored in:
  - Parquet metadata
  - filename/path conventions
- Annotations are written separately as sidecar files:
  - `*.annotations.json`

## What Should Be Done Before Training (PySpark Stage)

These tasks can be deferred to a PySpark pre-training job after loading processed Parquet files:

- Patient-group split assignment (leakage-safe train/val/test).
- Dedup policy decisions.
- Windowing/segmentation/padding strategy.
- Final cohort balancing and sampling.

Why this split is recommended:
- Keep ingestion focused on deterministic parsing + canonical label normalization.
- Keep training-time data strategy flexible and experiment-specific.
