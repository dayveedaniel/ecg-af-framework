# ECG Data Pipeline — Technical Specification v4

## Changelog (v3 → v4)

1. **Removed `splits` block from manifest schema.** The pipeline no longer reads or preserves predefined train/val/test splits from source datasets. All split assignment is deferred to experiment code. This simplifies the manifest, the registrar, and eliminates complexity around heterogeneous split encoding methods across datasets.

2. **Removed `split_source` column from `recordings` table.** The `split` column remains but is always NULL after ingestion. Experiment code populates it later via SQL UPDATE.

3. **Fixed `af_present` semantics.** Changed from `BOOLEAN DEFAULT FALSE` to `BOOLEAN DEFAULT NULL`. Three-state semantics: `TRUE` = AF annotated in this recording, `FALSE` = rhythm annotations exist but no AF found, `NULL` = no rhythm annotations available (unknown). Previously, unannotated recordings were incorrectly marked as "no AF" when the truth was "we don't know."

4. **Updated design principles.** Replaced "Split-safe" principle (which referenced preserving original splits) with "Split-deferred" principle (pipeline ingests all data uniformly; splits assigned at experiment time).

5. **Updated example queries.** Replaced split-based training set query with AF distribution analysis query. Added query demonstrating the three-state `af_present` semantics.

6. **Simplified `MetadataRegistrar.register_recordings()` interface.** Removed `manifest` parameter since split extraction logic is no longer needed.

7. **Updated Key Decisions Summary.** Decision #4 rewritten to reflect split-deferred approach. Added decision #13 about `af_present` three-state semantics.

---

## 1. Project Overview

### 1.1 Purpose

This pipeline ingests, normalizes, and registers ECG datasets from diverse public and proprietary sources into a unified storage and metadata system. It supports a thesis research project titled "A Comparative Study of Signal Processing and Machine Learning Algorithms for Atrial Fibrillation Detection in Long-Term ECG Monitoring" at Innopolis University, in partnership with Cardioway.

The pipeline must handle 42+ ECG databases in various formats (WFDB, CSV, MAT, HDF5, etc.) with different sampling rates (250–1000 Hz), channel configurations, and annotation schemas. The output is a clean, standardized data lake on S3-compatible storage (MinIO → AWS S3) with a PostgreSQL metadata catalog.

### 1.2 Research Focus

The pipeline serves **atrial fibrillation (AF) detection** — classifying AF episodes in continuous ECG recordings. All dataset selection, annotation handling, and processing decisions are driven by this goal.

Noise detection and artifact handling are addressed as a **preprocessing concern** (noisy segments must be identified and handled to avoid corrupting AF classification), not as an independent research direction.

### 1.3 Design Principles

- **Modular**: each stage is an independent Python module with a clear interface
- **Idempotent**: re-running any stage produces the same result without duplication
- **Format-agnostic**: file formats and sampling rates are inferred at read time, never hardcoded
- **Provenance-aware**: every processed file traces back to its source dataset, original file, and processing parameters
- **Split-deferred**: the pipeline ingests all data uniformly; train/val/test splits are assigned at experiment time, not during ingestion
- **Configurable**: all tunable parameters (target sample rate, normalization, etc.) live in config files, not hardcoded

### 1.4 Source Datasets

The pipeline currently targets the following datasets (from the existing download script):

| Dataset | Provider |
|---------|----------|
| MIT-BIH Arrhythmia Database | PhysioNet S3 |
| MIT-BIH Atrial Fibrillation Database | PhysioNet S3 |
| Long-Term Atrial Fibrillation Database (LTAFDB) | PhysioNet S3 |
| SHDB-AF Database (Japan) | PhysioNet S3 |
| PhysioNet/CinC Challenge 2017 (AF Classification) | PhysioNet S3 |
| CPSC 2021 Paroxysmal AF Event Detection | PhysioNet S3 |
| CPSC 2020 Classification of 12-lead ECGs | PhysioNet S3 |
| CPSC 2021 Varying Dimensions in Electrocardiography | PhysioNet S3 |
| PTB-XL Electrocardiography Dataset | PhysioNet S3 |
| AF Termination Challenge Database | PhysioNet S3 |
| St Petersburg INCART 12-lead Arrhythmia Database | PhysioNet S3 |
| Synthetic ECG Dataset (Clean vs Noisy) | Zenodo |
| ECG in High-Intensity Exercise Dataset | Zenodo |
| ISP ECG Delineation Dataset | Zenodo |
| CODE-15% ECG Dataset (Brazil) | Zenodo |
| CODE-Test (Gold-Standard Subset) | Zenodo |
| IRIDIA-AF Database | Zenodo |
| Sami-Trop ECG Dataset | Zenodo |
| Chapman-Shaoxing 12-Lead ECG Database | Figshare |
| CPSC 2018 ECG Challenge Dataset (Validation Set) | Direct URL |

---

## 2. Architecture Overview

### 2.1 Pipeline Stages

```
┌─────────────┐    ┌──────────────────────────────────┐    ┌─────────────┐
│  DOWNLOAD    │───▶│     READ, PARSE, RESAMPLE &      │───▶│  REGISTER   │
│  & EXTRACT   │    │     WRITE TO PARQUET              │    │  METADATA   │
└─────────────┘    └──────────────────────────────────┘    └─────────────┘
    Module 1                    Module 2                       Module 3
      │                           │                              │
      ▼                           ▼                              ▼
  s3://raw/                  s3://processed/                 PostgreSQL
```

Module 2 combines reading, resampling, and writing into a single processing step. There is no intermediate storage — raw files go in, processed Parquet files come out.

### 2.2 Technology Stack

| Component         | Technology                | Role                                          |
|-------------------|---------------------------|-----------------------------------------------|
| Orchestration     | Apache Airflow            | DAG-based task scheduling, retries, monitoring |
| Parallel compute  | PySpark                   | Distributed file-level processing              |
| Object storage    | MinIO (S3-compatible)     | Data lake for all ECG data (raw + processed)   |
| Metadata database | PostgreSQL                | Recording metadata, dataset registry, split info |
| Containerization  | Docker + Docker Compose   | All services run in containers                 |
| Cloud storage     | AWS S3 (production)       | MinIO points to S3 via endpoint swap           |

### 2.3 Container Layout (Docker Compose)

```yaml
services:
  airflow-webserver:    # Airflow UI on port 8080
  airflow-scheduler:    # DAG scheduling
  airflow-worker:       # Task execution (includes PySpark)
  postgres:             # Metadata DB + Airflow backend DB (separate schemas)
  minio:                # S3-compatible storage on port 9000 (console: 9001)
```

All containers share a Docker network. Airflow worker has PySpark installed locally and submits jobs in local mode (no separate Spark cluster needed for initial deployment).

### 2.4 S3 Bucket Structure

```
s3://ecg-pipeline/
├── raw/                              # Module 1 output: downloaded & extracted files
│   ├── mit-bih-arrhythmia/           # One directory per dataset (slug from manifest)
│   │   ├── 100.hea
│   │   ├── 100.dat
│   │   ├── 100.atr
│   │   ├── 100.qrs
│   │   └── ...
│   ├── mit-bih-atrial-fibrillation-database/
│   └── ...
│
├── processed/                        # Module 2 output: resampled Parquet + annotations
│   ├── 250hz/                        # Primary target rate (from pipeline_config.yaml)
│   │   ├── mit-bih-arrhythmia/
│   │   │   ├── 100.parquet
│   │   │   ├── 100.annotations.json
│   │   │   └── ...
│   │   ├── mit-bih-atrial-fibrillation-database/
│   │   │   ├── 04015.parquet
│   │   │   ├── 04015.annotations.json
│   │   │   └── ...
│   │   └── ...
│   │
│   └── 500hz/                        # Additional rates created on-demand by resample DAG
│       └── ...
│
└── manifests/                        # Dataset manifest YAML files
    ├── mit-bih-arrhythmia.yaml
    ├── mit-bih-atrial-fibrillation-database.yaml
    └── ...
```

**Note on splits**: Train/val/test split assignments are stored **only in PostgreSQL**, not in the folder structure. All recordings for a dataset live flat under `processed/{rate}hz/{dataset_name}/`. The pipeline does **not** read or preserve predefined splits from source datasets. The `recordings.split` column is NULL after ingestion; experiment code populates it later based on the researcher's stratification strategy (e.g., balanced AF/non-AF ratios, geographic diversity). This ensures consistent split methodology across all datasets.

**Note on additional sample rates**: The primary pipeline always produces output at the default target rate (250 Hz, configured in `pipeline_config.yaml`). A separate on-demand DAG (`ecg_resample_existing`) can read from an existing `processed/{rate}hz/` folder and produce output at a different rate under `processed/{new_rate}hz/`. This avoids re-downloading and re-parsing raw files.

---

## 3. Configuration

### 3.1 Pipeline Configuration File

All tunable parameters live in `pipeline_config.yaml` at the project root. This is the single source of truth for pipeline behavior. Code reads from this file; nothing is hardcoded.

```yaml
# pipeline_config.yaml

pipeline:
  s3_bucket: "ecg-pipeline"
  default_target_sample_rate: 250         # Hz — all data resampled to this rate
  default_normalization: "none"           # "none", "zscore", "minmax"
  parquet_compression: "snappy"           # Parquet compression codec

processing:
  min_recordings_for_spark: 50            # Use PySpark only if dataset has >= this many recordings
  spark_driver_memory: "8g"               # Otherwise use simple Python loop
  spark_executor_memory: "4g"

airflow:
  max_active_datasets: 3                  # Max datasets processing concurrently
  task_retries: 3                         # Retry count for failed tasks
  retry_delay_seconds: 300                # Base delay between retries
  retry_exponential_backoff: true         # Exponential backoff on retries

download:
  timeout_seconds: 60                     # HTTP timeout per request
  chunk_size_bytes: 1048576               # 1 MB download chunks
  sleep_between_datasets_seconds: 0.2     # Politeness delay between datasets
  physionet_s3_bucket: "physionet-open"   # PhysioNet public S3 bucket name
```

### 3.2 Environment Variables

Environment-specific settings that change per deployment (local vs production) live in `.env`, not in `pipeline_config.yaml`.

```bash
# .env

# S3 / MinIO
S3_ENDPOINT_URL=http://minio:9000      # Change to https://s3.amazonaws.com for AWS
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin

# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=ecg_metadata
POSTGRES_USER=ecg_pipeline
POSTGRES_PASSWORD=<secure_password>

# Airflow (uses same Postgres instance, different database)
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:airflow@postgres:5432/airflow

# Spark
SPARK_MASTER=local[*]                  # Use all available cores

# Kaggle (optional — only needed if Kaggle datasets are added in the future)
KAGGLE_USERNAME=
KAGGLE_KEY=
```

---

## 4. Dataset Manifest Schema

Each dataset has a YAML manifest file. This is the **only** configuration needed to add a new dataset to the pipeline.

### 4.1 What Goes in the Manifest (Manual) vs What Is Inferred (Runtime)

**Filled manually when creating the manifest** (requires reading the dataset's documentation):

- `dataset.name`, `dataset.display_name` — dataset identifier
- `source.url`, `source.provider` — where and how to download
- `metadata.description`, `metadata.citation`, `metadata.license`, `metadata.geographic_origin` — provenance
- `annotations.available`, `annotations.types` — what kind of labels exist
- `quirks` — any dataset-specific handling notes

**Inferred at runtime by the reader** (never in the manifest):

- Sampling rate
- File format
- Number of channels and channel names
- Signal units
- Duration
- Number of samples

**Optional fields filled if known** (help with validation but not required):

- `metadata.num_subjects`, `metadata.num_recordings` — if the reader finds fewer, it logs a warning
- `metadata.leads` — known lead types (for documentation only; actual detection is at read time)

### 4.2 Schema Definition

```yaml
# manifests/mit-bih-atrial-fibrillation-database.yaml

dataset:
  name: "mit-bih-atrial-fibrillation-database"   # Unique identifier (used as directory name)
  display_name: "MIT-BIH Atrial Fibrillation Database"
  version: "1.0.0"

source:
  url: "https://physionet.org/content/afdb/1.0.0/"
  provider: "physionet_s3"              # One of: physionet_s3, zenodo, figshare, direct_url
                                        # If omitted, inferred from URL using dispatch logic

metadata:
  description: "25 long-term (10-hour) two-channel ECG recordings of subjects with AF"
  citation: "Moody G. A new method for detecting atrial fibrillation..."
  license: "ODC-BY 1.0"
  geographic_origin: "United States"
  num_subjects: 25                      # Optional — used for validation
  num_recordings: 25                    # Optional — used for validation
  leads: ["ECG1", "ECG2"]              # Optional — for documentation

annotations:
  available: true
  types:                                # What kind of annotations exist
    - beat_labels                       # Individual beat classifications
    - rhythm_labels                     # Rhythm episode annotations (AF, normal, etc.)

quirks: []                              # List of dataset-specific handling notes
  # Examples:
  # - "annotations use non-standard beat labels: N=normal, A=AF"
  # - "lead names are numeric (0, 1) instead of standard names"
  # - "first 5 seconds of each recording contain calibration signal"
```

### 4.3 Adding a New Dataset

To add a new dataset to the pipeline:

1. Read the dataset's documentation page (PhysioNet page, Zenodo record, Figshare listing, paper, etc.)
2. Create a YAML manifest file in `manifests/` — fill in the manual fields (~5 minutes per dataset)
3. Trigger the Airflow DAG with `dataset_name` set to the manifest name
4. The pipeline handles everything else: download, format detection, parsing, resampling, registration

---

## 5. Module 1 — Downloader & Extractor

### 5.1 Responsibility

Download dataset files from their source and extract archives if needed. Write raw files to `s3://ecg-pipeline/raw/{dataset_name}/`.

This module is a refactored version of the existing download script (`fetch_datasets.py`), adapted to write to S3 instead of local disk and integrated with Airflow.

### 5.2 Interface

```python
class Downloader:
    """Downloads and extracts a dataset based on its manifest."""

    def __init__(self, manifest: DatasetManifest, s3_client: S3Client):
        ...

    def run(self) -> DownloadResult:
        """
        Downloads the dataset and uploads raw files to S3.

        Returns:
            DownloadResult with:
                - dataset_name: str
                - s3_prefix: str (e.g., "raw/mit-bih-atrial-fibrillation-database/")
                - file_count: int
                - total_size_bytes: int
                - download_timestamp: datetime
        """
        ...

    def is_already_downloaded(self) -> bool:
        """Check if raw/ prefix already contains files for this dataset (idempotency)."""
        ...
```

### 5.3 Download Strategies

Implement a strategy pattern based on `source.provider` in the manifest. If `provider` is omitted, infer it from the URL using the same dispatch logic as the existing download script (`dispatch_fetch`).

The following four strategies correspond directly to the providers in the current download script:

**`physionet_s3`**

- Triggered by: URLs containing `physionet.org/content/` or `physionet.org/files/`
- Method: Parse dataset name and version from URL, construct the S3 prefix `{dataset}/{version}/`, then use boto3 with unsigned requests to paginate through all objects in the `physionet-open` public bucket and download them.
- Dependency: boto3, botocore
- Note: This downloads from AWS S3 (PhysioNet's public bucket) to our MinIO/S3. It is an S3-to-S3 transfer when running in production on AWS.

**`zenodo`**

- Triggered by: URLs containing `zenodo.org`
- Method: Parse the record ID from the URL, hit the Zenodo records API (`https://zenodo.org/api/records/{record_id}`), iterate over the `files` array, and download each file via its `links.self` URL.
- No authentication needed for public records.

**`figshare`**

- Triggered by: URLs containing `figshare.com`
- Method: Detect whether the URL points to an article or a collection.
  - Article: Hit `https://api.figshare.com/v2/articles/{id}`, download files from the `files` array using each file's `download_url`.
  - Collection: Hit `https://api.figshare.com/v2/collections/{id}/articles`, then fetch files from each article. Files from each article are stored in a subdirectory named by the article's slugified title.
- No authentication needed for public articles/collections.

**`direct_url`**

- Triggered by: Any URL not matching the above providers
- Method: Download the file directly from the URL. The filename is extracted from the URL path. Used for standalone file links like the CPSC 2018 validation set zip.
- Archive files are auto-detected by filename extension (`.zip`, `.tar.gz`, `.tgz`, `.gz`) and extracted automatically.

### 5.4 Archive Extraction

After download, if a file has a supported archive extension:

- `zip`: use `zipfile` Python module
- `tar.gz` / `tgz`: use `tarfile` Python module
- `gz`: decompress individual `.gz` files with `gzip`

Upload extracted files to S3 and delete the archive from S3 to save storage.

### 5.5 Raw File Hygiene

After download and extraction complete, Module 1 cleans the raw prefix:

- Keep only files with reader-supported extensions:
  - WFDB: `.hea`, `.dat`, `.atr`, `.qrs`
  - CSV: `.csv`
  - MAT: `.mat`
  - HDF5: `.h5`, `.hdf5`
- Delete all other files (READMEs, checksums, license text, images, archives, etc.)
- If any folder-marker objects become empty after file deletion, delete those markers too

This ensures `raw/{dataset_name}/` only contains files that Module 2 can process.

### 5.6 Idempotency

Before downloading, check if `s3://ecg-pipeline/raw/{dataset_name}/` already has files. If it does:

- If file count matches expected (from a previous successful run), skip entirely.
- Support a `force_redownload` flag (Airflow DAG param) to re-download.

This mirrors the existing script's `has_downloaded_files` check.

### 5.7 Error Handling

- **Retries are handled by Airflow**, not by custom retry logic in the downloader. Airflow task configuration uses `retries`, `retry_delay`, and `retry_exponential_backoff` from `pipeline_config.yaml`. The downloader raises an exception on failure and Airflow handles the rest.
- Log failed files but don't fail the entire dataset if some files fail (partial success).
- Write a `_download_report.json` to the raw prefix with download status per file (same structure as the existing script's manifest).

### 5.8 Compute Requirements

- **No Spark needed.** This is I/O-bound, not compute-bound.
- Runs as a standard Python Airflow task (PythonOperator).
- Downloads happen sequentially per dataset (network-bound) but multiple datasets can run in parallel via Airflow concurrency.

### 5.9 Migration from Existing Script

The existing `fetch_datasets.py` script contains working download logic for four providers. The refactoring needed is:

1. Extract provider-specific functions (`fetch_physionet`, `fetch_zenodo`, `fetch_figshare`, `fetch_direct`) into separate strategy classes under `src/downloader/strategies/`.
2. Replace local filesystem writes (`Path`) with S3 uploads via the `S3Client` wrapper.
3. Replace the hardcoded `DATASET_LINKS` list with manifest YAML loading.
4. Replace the script's custom retry logic with Airflow's task retry mechanism.
5. Keep the existing URL parsing helpers (`zenodo_record_id`, `figshare_article_id`, `figshare_collection_id`, `_parse_physionet_dataset_base`) — they work correctly.
6. Keep the `slugify` and `human_size` utility functions.

---

## 6. Module 2 — Reader, Parser & Processor

### 6.1 Responsibility

This is the core module. It performs the full transformation from raw files to processed Parquet in a single pass:

1. **Discover** files in `s3://ecg-pipeline/raw/{dataset_name}/` and group them into logical recordings
2. **Detect** file format and dispatch to the appropriate reader
3. **Read** signal data and annotations, infer sampling rate, channels, and units
4. **Normalize** signal values to millivolts (mV)
5. **Resample** to the target sampling rate (from `pipeline_config.yaml`, default 250 Hz)
6. **Write** Parquet + annotation JSON to `s3://ecg-pipeline/processed/{rate}hz/{dataset_name}/`

### 6.2 Unified Data Model

Every recording, regardless of source format, is parsed into this in-memory representation before writing to Parquet. This model exists only in memory during processing — it is never stored as-is.

```python
@dataclass
class ECGRecording:
    """Unified in-memory representation of a single ECG recording."""

    recording_id: str               # Unique ID: "{dataset_name}/{base_filename}"
    dataset_name: str
    original_filename: str          # Original file basename (e.g., "100")

    # Signal data
    signal: np.ndarray              # Shape: (num_samples, num_channels), dtype: float64
                                    # Values in millivolts (mV), physical units

    # Signal metadata (all inferred from file)
    sampling_rate: int              # Original sampling rate in Hz (before resampling)
    num_channels: int
    channel_names: list[str]        # e.g., ["MLII", "V1"]
    duration_seconds: float
    num_samples: int
    units: str                      # Always "mV" after normalization

    # Annotations (optional — not all recordings have them)
    annotations: ECGAnnotations | None

    # Subject metadata (when available in the source file)
    subject_id: str | None
    age: int | None
    sex: str | None                 # "M", "F", or None

    # Provenance
    source_format: str              # Detected format: "wfdb", "csv", "mat", "hdf5"


@dataclass
class ECGAnnotations:
    """Unified annotation representation."""

    # Beat-level annotations
    beat_positions: np.ndarray | None       # Sample indices of annotated beats
    beat_labels: list[str] | None           # One label per beat

    # Rhythm-level annotations (episodes)
    rhythm_episodes: list[RhythmEpisode] | None

    # Delineation annotations (P, QRS, T boundaries) — rarely available
    delineation: ECGDelineation | None


@dataclass
class RhythmEpisode:
    """A contiguous episode of a specific rhythm."""
    start_sample: int
    end_sample: int
    rhythm_type: str               # Standardized: "normal", "af", "aflutter", "other"
    original_label: str            # The raw label from the source dataset


@dataclass
class ECGDelineation:
    """Wave boundary annotations for ECG morphology."""
    p_onsets: np.ndarray | None    # Sample indices
    p_offsets: np.ndarray | None
    qrs_onsets: np.ndarray | None
    qrs_offsets: np.ndarray | None
    t_onsets: np.ndarray | None
    t_offsets: np.ndarray | None
```

### 6.3 Output Format

**Signal data**: Apache Parquet files.

Each recording produces one `.parquet` file containing:

```
Columns:
  - sample_index: int64           # 0, 1, 2, ... (row index)
  - channel_0: float64            # Signal values in mV for first channel
  - channel_1: float64            # Signal values in mV for second channel (if exists)
  - ...                           # One column per channel, all channels preserved

Parquet metadata (key-value pairs stored in file footer):
  - recording_id: str
  - dataset_name: str
  - sampling_rate: str            # The resampled rate (e.g., "250")
  - original_sampling_rate: str   # The rate before resampling (e.g., "360")
  - num_channels: str
  - channel_names: str            # JSON array, e.g., '["MLII", "V1"]'
  - duration_seconds: str
  - units: "mV"
  - source_format: str
  - original_filename: str
```

Rationale for Parquet: columnar format allows reading individual channels without loading the entire file (useful for lead selection at training time). Metadata in the footer means you can read recording properties without reading the signal. Compression (snappy by default) reduces S3 storage costs. PySpark reads Parquet natively.

**Annotations**: JSON sidecar files.

Each recording with annotations produces a `.annotations.json` file. Sample indices in annotations are adjusted to match the resampled signal.

```json
{
  "recording_id": "mit-bih-atrial-fibrillation-database/04015",
  "annotation_source": "original",
  "target_sampling_rate": 250,
  "original_sampling_rate": 250,
  "beat_annotations": {
    "positions": [77, 370, 662],
    "labels": ["N", "N", "A"]
  },
  "rhythm_episodes": [
    {
      "start_sample": 0,
      "end_sample": 450000,
      "rhythm_type": "af",
      "original_label": "(AFIB"
    },
    {
      "start_sample": 450001,
      "end_sample": 900000,
      "rhythm_type": "normal",
      "original_label": "(N"
    }
  ],
  "delineation": null
}
```

### 6.4 Reader Interface & Format Discovery

```python
class BaseReader(ABC):
    """Abstract base class for format-specific readers."""

    @abstractmethod
    def can_read(self, filepath: str) -> bool:
        """Return True if this reader can handle the given file."""
        ...

    @abstractmethod
    def read(self, filepath: str, s3_client: S3Client) -> ECGRecording:
        """Read the file and return a unified ECGRecording."""
        ...

    @abstractmethod
    def discover_recordings(self, file_list: list[str]) -> list[RecordingFileGroup]:
        """
        Given a flat list of files in a dataset directory, group them into
        logical recordings and return the groups.

        For WFDB: groups 100.hea + 100.dat + 100.atr + 100.qrs into one RecordingFileGroup
        For CSV: each .csv is typically one recording
        For MAT: each .mat is typically one recording
        For HDF5: each .h5/.hdf5 is typically one recording
        """
        ...


@dataclass
class RecordingFileGroup:
    """A group of files that constitute one logical recording."""
    recording_base_name: str        # e.g., "100"
    primary_file: str               # The main file to open (e.g., "100.hea")
    associated_files: list[str]     # All related files (e.g., ["100.dat", "100.atr", "100.qrs"])
    detected_format: str            # "wfdb", "csv", "mat", "hdf5"
```

```python
class FormatDispatcher:
    """Discovers file formats and dispatches to appropriate readers."""

    def __init__(self, readers: list[BaseReader]):
        self.readers = readers

    def discover_and_group(self, file_list: list[str]) -> list[RecordingFileGroup]:
        """
        Given all files in a dataset's raw/ directory:
        1. Classify files by extension (and header magic bytes as fallback)
        2. Determine the dominant format(s) in the dataset
        3. Group files into logical recordings using the appropriate reader
        4. Return the groups, each tagged with its detected format

        Ignore non-ECG files: README, LICENSE, RECORDS, ANNOTATORS,
        SHA256SUMS.txt, .txt descriptions, .png/.jpg images, etc.
        """
        ...
```

### 6.5 Format-Specific Readers to Implement

| Reader Class     | Handles Extensions                    | Key Library     | Notes                                                  |
|------------------|---------------------------------------|-----------------|--------------------------------------------------------|
| `WFDBReader`     | `.hea`, `.dat`, `.atr`, `.qrs`        | `wfdb`          | PhysioNet standard. Groups files by base name. `.qrs` files are QRS-specific annotation files read via `wfdb.rdann(record_name, extension='qrs')`. Most datasets in the pipeline use this format. |
| `CSVReader`      | `.csv`                                | `pandas`        | Columns vary by dataset. Must infer which columns are signal vs. metadata. Sampling rate may be in a header comment or companion file. |
| `MATReader`      | `.mat`                                | `scipy.io`      | MATLAB files. Structure varies widely. Must inspect variable names to find signal data, annotations, and sampling rate. |
| `HDF5Reader`     | `.h5`, `.hdf5`                        | `h5py`          | Hierarchical format. Must traverse groups to find signal and metadata. |

### 6.6 Unit Normalization

All readers must convert signal values to **millivolts (mV)** in physical units as part of the read step:

- **WFDB**: apply gain and baseline from `.hea` file (`wfdb.rdrecord(physical=True)` does this automatically)
- **CSV**: check if values are ADC counts (integers) or physical units. If ADC, look for gain/offset info in header or companion file. If ambiguous, store as-is and flag `units: "unknown"` in metadata.
- **MAT**: look for gain/scaling variables in the MAT file structure
- **HDF5**: look for gain/scaling attributes in the HDF5 group metadata

### 6.7 Resampling

Resampling is performed after reading and unit normalization, before writing to Parquet. The target rate comes from `pipeline_config.yaml` (`pipeline.default_target_sample_rate`).

```python
def resample_signal(signal: np.ndarray,
                    original_rate: int,
                    target_rate: int) -> np.ndarray:
    """
    Resample a multi-channel signal.

    Args:
        signal: shape (num_samples, num_channels)
        original_rate: original sampling rate in Hz
        target_rate: desired sampling rate in Hz (from pipeline_config.yaml)

    Returns:
        Resampled signal with shape (new_num_samples, num_channels)

    Method:
        Use scipy.signal.resample_poly for rational resampling.
        This applies an anti-aliasing FIR filter and resamples by a
        rational factor (up/down) which is exact for integer rate ratios.

    If original_rate == target_rate, return the signal unchanged (no-op).
    """
    ...
```

### 6.8 Annotation Adjustment

When resampling, all sample-indexed annotations must be rescaled:

```python
def adjust_annotations(annotations: ECGAnnotations,
                       original_rate: int,
                       target_rate: int) -> ECGAnnotations:
    """
    Rescale all sample indices in annotations to match the new sampling rate.

    new_sample = round(old_sample * target_rate / original_rate)

    This applies to:
    - beat_positions
    - rhythm_episode start_sample and end_sample
    - delineation onset/offset arrays

    If original_rate == target_rate, return annotations unchanged (no-op).
    """
    ...
```

### 6.9 Processing Pipeline (per recording)

The full processing flow for a single recording:

```python
def process_recording(file_group: RecordingFileGroup,
                      reader: BaseReader,
                      target_rate: int,
                      s3_client: S3Client) -> ProcessingResult:
    """
    Full pipeline for one recording:
    1. Read raw file(s) → ECGRecording (signal in mV, original sample rate)
    2. Resample signal to target_rate
    3. Adjust annotation sample indices to target_rate
    4. Write signal to Parquet with metadata in footer
    5. Write annotations to JSON sidecar
    6. Return metadata for PostgreSQL registration
    """
    # Step 1: Read
    recording = reader.read(file_group.primary_file, s3_client)

    # Step 2: Resample
    original_rate = recording.sampling_rate
    if original_rate != target_rate:
        recording.signal = resample_signal(recording.signal, original_rate, target_rate)
        recording.num_samples = recording.signal.shape[0]
        recording.duration_seconds = recording.num_samples / target_rate

    # Step 3: Adjust annotations
    if recording.annotations is not None and original_rate != target_rate:
        recording.annotations = adjust_annotations(
            recording.annotations, original_rate, target_rate
        )

    # Step 4: Write Parquet
    parquet_path = write_parquet(recording, target_rate, s3_client)

    # Step 5: Write annotations JSON
    annotations_path = None
    if recording.annotations is not None:
        annotations_path = write_annotations_json(
            recording, target_rate, original_rate, s3_client
        )

    # Step 6: Return metadata
    return ProcessingResult(
        recording_id=recording.recording_id,
        parquet_path=parquet_path,
        annotations_path=annotations_path,
        sampling_rate=target_rate,
        original_sampling_rate=original_rate,
        num_channels=recording.num_channels,
        channel_names=recording.channel_names,
        duration_seconds=recording.duration_seconds,
        num_samples=recording.num_samples,
        source_format=recording.source_format,
        subject_id=recording.subject_id,
        age=recording.age,
        sex=recording.sex,
        has_beat_annotations=recording.annotations is not None
            and recording.annotations.beat_positions is not None,
        has_rhythm_annotations=recording.annotations is not None
            and recording.annotations.rhythm_episodes is not None,
        af_present=_check_af_present(recording.annotations),
    )
```

### 6.10 Compute Requirements

- **Uses PySpark for large datasets.** Each recording is independent, so processing is embarrassingly parallel. Spark reads the file list, distributes `RecordingFileGroup` objects across executors, each executor runs `process_recording()`.
- **Uses simple Python loop for small datasets.** If the dataset has fewer than `processing.min_recordings_for_spark` recordings (from `pipeline_config.yaml`), skip Spark overhead and process sequentially.
- Runs as a PythonOperator in Airflow. The callable internally creates a SparkSession when needed.

### 6.11 Error Handling

- If a single recording fails to parse, log the error and continue. Do not fail the entire dataset.
- Write a `_processing_report.json` to the processed prefix listing success/failure per recording with error messages.
- Common failure modes: corrupted binary data, unexpected file structure, missing companion files, unsupported encoding.
- **Task-level retries are handled by Airflow** (configured in `pipeline_config.yaml`).

---

## 7. Module 3 — Metadata Registrar

### 7.1 Responsibility

After Module 2 completes for a dataset, read the Parquet metadata footers and annotation files, and insert structured metadata into PostgreSQL. This creates the queryable catalog that downstream experiments use to find data.

### 7.2 Database Schema

```sql
-- Dataset-level information (one row per dataset)
CREATE TABLE datasets (
    dataset_id          SERIAL PRIMARY KEY,
    name                VARCHAR(255) UNIQUE NOT NULL,    -- e.g., "mit-bih-atrial-fibrillation-database"
    display_name        VARCHAR(512),
    description         TEXT,
    source_url          VARCHAR(1024),
    license             VARCHAR(255),
    citation            TEXT,
    geographic_origin   VARCHAR(255),
    num_subjects        INTEGER,
    num_recordings      INTEGER,                         -- Actual count from processing
    has_beat_annotations        BOOLEAN DEFAULT FALSE,
    has_rhythm_annotations      BOOLEAN DEFAULT FALSE,
    has_delineation_annotations BOOLEAN DEFAULT FALSE,
    ingestion_timestamp TIMESTAMPTZ DEFAULT NOW(),
    pipeline_version    VARCHAR(50)
);

-- Recording-level information (one row per recording)
CREATE TABLE recordings (
    recording_id        SERIAL PRIMARY KEY,
    dataset_id          INTEGER REFERENCES datasets(dataset_id),
    recording_name      VARCHAR(255) NOT NULL,           -- e.g., "04015"
    full_recording_id   VARCHAR(512) UNIQUE NOT NULL,    -- e.g., "mit-bih-atrial-fibrillation-database/04015"

    -- Signal properties
    sampling_rate       INTEGER NOT NULL,                -- Target rate after resampling (e.g., 250)
    original_sampling_rate INTEGER NOT NULL,             -- Rate before resampling (e.g., 250)
    num_channels        INTEGER NOT NULL,
    channel_names       TEXT[],                          -- e.g., {"ECG1", "ECG2"}
    duration_seconds    FLOAT NOT NULL,
    num_samples         BIGINT NOT NULL,
    source_format       VARCHAR(50),                    -- "wfdb", "csv", "mat", "hdf5"

    -- Subject info (nullable — not all datasets provide this)
    subject_id          VARCHAR(255),
    age                 INTEGER,
    sex                 VARCHAR(10),

    -- Annotation availability
    has_beat_annotations        BOOLEAN DEFAULT FALSE,
    has_rhythm_annotations      BOOLEAN DEFAULT FALSE,
    has_delineation_annotations BOOLEAN DEFAULT FALSE,
    num_beat_annotations        INTEGER DEFAULT 0,
    num_rhythm_episodes         INTEGER DEFAULT 0,
    af_present                  BOOLEAN DEFAULT NULL,    -- Three-state: TRUE = AF annotated,
                                                         -- FALSE = rhythm annotations exist but no AF,
                                                         -- NULL = no rhythm annotations (unknown)

    -- File paths in S3
    parquet_path                VARCHAR(1024),           -- s3://ecg-pipeline/processed/250hz/...
    annotations_path            VARCHAR(1024),           -- s3://ecg-pipeline/processed/250hz/...annotations.json

    -- Dataset split assignment
    -- Always NULL after pipeline ingestion. Populated later by experiment code
    -- via SQL UPDATE based on the researcher's stratification strategy.
    split                       VARCHAR(20),             -- "train", "val", "test", or null

    -- Provenance
    ingestion_timestamp TIMESTAMPTZ DEFAULT NOW(),
    pipeline_version    VARCHAR(50)
);

-- Resampled variants (created by the on-demand resample DAG)
-- Only populated when additional sample rates are produced beyond the default.
CREATE TABLE resampled_recordings (
    id                  SERIAL PRIMARY KEY,
    recording_id        INTEGER REFERENCES recordings(recording_id),
    target_sampling_rate INTEGER NOT NULL,
    parquet_path        VARCHAR(1024) NOT NULL,
    annotations_path    VARCHAR(1024),
    processing_timestamp TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(recording_id, target_sampling_rate)
);

-- Indexes for common queries
CREATE INDEX idx_recordings_dataset ON recordings(dataset_id);
CREATE INDEX idx_recordings_af ON recordings(af_present) WHERE af_present = TRUE;
CREATE INDEX idx_recordings_split ON recordings(split);
CREATE INDEX idx_recordings_sr ON recordings(sampling_rate);
CREATE INDEX idx_resampled_rate ON resampled_recordings(target_sampling_rate);
```

### 7.3 Example Queries

```sql
-- All recordings that contain AF episodes
SELECT r.full_recording_id, r.parquet_path, r.duration_seconds
FROM recordings r WHERE r.af_present = TRUE;

-- All recordings from a specific dataset
SELECT r.* FROM recordings r
JOIN datasets d ON r.dataset_id = d.dataset_id
WHERE d.name = 'mit-bih-atrial-fibrillation-database';

-- AF distribution across datasets (for designing stratified splits)
SELECT d.name, d.geographic_origin,
       COUNT(*) as total,
       SUM(CASE WHEN r.af_present = TRUE THEN 1 ELSE 0 END) as with_af,
       SUM(CASE WHEN r.af_present = FALSE THEN 1 ELSE 0 END) as without_af,
       SUM(CASE WHEN r.af_present IS NULL THEN 1 ELSE 0 END) as unknown
FROM recordings r
JOIN datasets d ON r.dataset_id = d.dataset_id
GROUP BY d.name, d.geographic_origin
ORDER BY total DESC;

-- Only recordings where AF status is known (annotated datasets only)
-- Use this as the basis for split assignment in experiment code
SELECT r.full_recording_id, r.parquet_path, r.af_present,
       d.geographic_origin
FROM recordings r
JOIN datasets d ON r.dataset_id = d.dataset_id
WHERE r.af_present IS NOT NULL;

-- After experiment code assigns splits: get the training set
SELECT r.full_recording_id, r.parquet_path, r.annotations_path
FROM recordings r WHERE r.split = 'train';

-- Dataset summary: recording counts by geographic origin
SELECT d.geographic_origin, COUNT(r.recording_id) as num_recordings,
       SUM(CASE WHEN r.af_present THEN 1 ELSE 0 END) as af_recordings
FROM recordings r
JOIN datasets d ON r.dataset_id = d.dataset_id
GROUP BY d.geographic_origin;

-- Find all recordings resampled to 500 Hz (from on-demand resample DAG)
SELECT r.full_recording_id, rr.parquet_path
FROM resampled_recordings rr
JOIN recordings r ON rr.recording_id = r.recording_id
WHERE rr.target_sampling_rate = 500;
```

### 7.4 Interface

```python
class MetadataRegistrar:
    """Registers dataset and recording metadata in PostgreSQL."""

    def __init__(self, db_connection: Connection, s3_client: S3Client):
        ...

    def register_dataset(self, manifest: DatasetManifest) -> int:
        """
        Insert or update dataset-level metadata from manifest.
        Returns dataset_id.
        Uses UPSERT (INSERT ... ON CONFLICT UPDATE) for idempotency.
        """
        ...

    def register_recordings(self, dataset_name: str,
                            processing_results: list[ProcessingResult]) -> int:
        """
        Insert recording-level rows from Module 2's processing results.
        The split column is left NULL — split assignment is deferred
        to experiment code.

        Returns count of recordings registered.
        Uses UPSERT for idempotency.
        """
        ...

    def register_resampled_variants(self, dataset_name: str,
                                     target_rate: int) -> int:
        """
        Register resampled recording variants produced by the on-demand
        resample DAG. Links back to the base recording entry.
        """
        ...
```

### 7.5 Compute Requirements

- **No Spark.** This is lightweight: reading Parquet metadata footers (no signal data) and inserting rows into Postgres.
- Runs as a standard Python Airflow task (PythonOperator).
- Uses `psycopg2` or `SQLAlchemy` for database access.

---

## 8. Airflow DAG Design

### 8.1 Main DAG: Per-Dataset Pipeline

Each dataset is processed by a parameterized DAG. The DAG is triggered with a `dataset_name` parameter and reads the corresponding manifest.

```
                    ┌──────────────┐
                    │ load_manifest │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   download    │  (Module 1)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  read_parse   │  (Module 2 — reads, resamples, writes Parquet)
                    │  _resample    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   register    │  (Module 3 — writes to PostgreSQL)
                    └──────────────┘
```

```python
# dags/ecg_pipeline_dag.py

from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

# Read retry config from pipeline_config.yaml
config = load_pipeline_config()

default_args = {
    "owner": "ecg-pipeline",
    "retries": config["airflow"]["task_retries"],
    "retry_delay": timedelta(seconds=config["airflow"]["retry_delay_seconds"]),
    "retry_exponential_backoff": config["airflow"]["retry_exponential_backoff"],
}

with DAG(
    dag_id="ecg_dataset_pipeline",
    default_args=default_args,
    schedule_interval=None,           # Triggered manually per dataset
    start_date=datetime(2025, 1, 1),
    catchup=False,
    params={
        "dataset_name": "",           # Required: which dataset to process
        "force_redownload": False,
        "force_reprocess": False,
    },
    tags=["ecg", "pipeline"],
) as dag:

    load_manifest = PythonOperator(
        task_id="load_manifest",
        python_callable=load_manifest_fn,
    )

    download = PythonOperator(
        task_id="download",
        python_callable=download_fn,
    )

    read_parse_resample = PythonOperator(
        task_id="read_parse_resample",
        python_callable=read_parse_resample_fn,
    )

    register = PythonOperator(
        task_id="register_metadata",
        python_callable=register_fn,
    )

    load_manifest >> download >> read_parse_resample >> register
```

### 8.2 Batch DAG: Process All Datasets

A convenience DAG that triggers the per-dataset DAG for all datasets (or a filtered subset):

```python
# dags/ecg_batch_pipeline_dag.py
#
# Reads all manifest YAML files from s3://ecg-pipeline/manifests/
# and triggers the per-dataset DAG for each one using TriggerDagRunOperator.
# Respects max_active_datasets from pipeline_config.yaml.
```

### 8.3 On-Demand Resample DAG

A separate DAG that reads already-processed Parquet files at one sample rate and produces output at a different sample rate. This avoids re-downloading and re-parsing raw files.

```
                    ┌──────────────────┐
                    │ list_processed    │  (scan processed/{source_rate}hz/{dataset}/)
                    │ _recordings      │
                    └──────┬───────────┘
                           │
                    ┌──────▼───────────┐
                    │ resample_to      │  (read Parquet, resample, write to new rate folder)
                    │ _new_rate        │
                    └──────┬───────────┘
                           │
                    ┌──────▼───────────┐
                    │ register         │  (insert into resampled_recordings table)
                    │ _variants        │
                    └──────────────────┘
```

```python
# dags/ecg_resample_dag.py

with DAG(
    dag_id="ecg_resample_existing",
    schedule_interval=None,
    params={
        "dataset_name": "",           # Which dataset to resample
        "source_rate": 250,           # Read from this rate folder
        "target_rate": 500,           # Write to this rate folder
    },
    tags=["ecg", "resample"],
) as dag:
    ...
```

---

## 9. Project File Structure

```
ecg-pipeline/
├── docker-compose.yaml                # Full stack: Airflow + Postgres + MinIO
├── Dockerfile                         # Airflow worker image with PySpark + Python deps
├── requirements.txt                   # Python dependencies
├── pipeline_config.yaml               # All tunable pipeline parameters
├── .env                               # Environment-specific settings (S3, Postgres creds)
│
├── dags/                              # Airflow DAGs
│   ├── ecg_pipeline_dag.py            # Per-dataset pipeline DAG
│   ├── ecg_batch_pipeline_dag.py      # Batch trigger DAG
│   └── ecg_resample_dag.py            # On-demand resample DAG
│
├── src/
│   ├── __init__.py
│   ├── config.py                      # Loads pipeline_config.yaml and .env
│   ├── manifest.py                    # DatasetManifest dataclass and YAML loader
│   │
│   ├── downloader/                    # Module 1
│   │   ├── __init__.py
│   │   ├── downloader.py              # Main Downloader class
│   │   └── strategies/
│   │       ├── __init__.py
│   │       ├── base.py                # BaseDownloadStrategy ABC
│   │       ├── physionet_s3.py        # PhysioNet public S3 bucket download
│   │       ├── zenodo.py              # Zenodo records API
│   │       ├── figshare.py            # Figshare articles/collections API
│   │       └── direct_url.py          # Direct file URL download
│   │
│   ├── reader/                        # Module 2 (read + resample + write)
│   │   ├── __init__.py
│   │   ├── dispatcher.py              # FormatDispatcher — discovery & routing
│   │   ├── models.py                  # ECGRecording, ECGAnnotations, etc.
│   │   ├── processor.py               # process_recording() — full per-recording pipeline
│   │   ├── resampler.py               # resample_signal(), adjust_annotations()
│   │   ├── writer.py                  # Writes ECGRecording to Parquet + JSON
│   │   └── formats/
│   │       ├── __init__.py
│   │       ├── base.py                # BaseReader ABC
│   │       ├── wfdb_reader.py
│   │       ├── csv_reader.py
│   │       ├── mat_reader.py
│   │       └── hdf5_reader.py
│   │
│   ├── registrar/                     # Module 3
│   │   ├── __init__.py
│   │   ├── registrar.py               # MetadataRegistrar class
│   │   └── schema.sql                 # PostgreSQL schema (CREATE TABLE statements)
│   │
│   └── utils/
│       ├── __init__.py
│       ├── s3.py                      # S3Client wrapper (boto3)
│       ├── spark.py                   # SparkSession factory with S3A configuration
│       └── helpers.py                 # slugify(), human_size(), and other shared utilities
│
├── manifests/                         # Dataset manifests (YAML files)
│   ├── mit-bih-arrhythmia.yaml
│   ├── mit-bih-atrial-fibrillation-database.yaml
│   ├── ltafdb.yaml
│   ├── shdb-af.yaml
│   ├── physionet-cinc-challenge-2017.yaml
│   ├── cpsc2021-paroxysmal-af.yaml
│   ├── cpsc2020-12lead.yaml
│   ├── cpsc2021-varying-dimensions.yaml
│   ├── ptb-xl.yaml
│   ├── af-termination-challenge.yaml
│   ├── incartdb.yaml
│   ├── synthetic-ecg-clean-noisy.yaml
│   ├── tele-ecg-database.yaml
│   ├── ecg-high-intensity-exercise.yaml
│   ├── isp-ecg-delineation.yaml
│   ├── code-15-percent.yaml
│   ├── code-test-gold-standard.yaml
│   ├── iridia-af.yaml
│   ├── sami-trop.yaml
│   ├── chapman-shaoxing-12lead.yaml
│   └── cpsc2018-validation.yaml
│
├── tests/                             # Unit and integration tests
│   ├── test_downloader.py
│   ├── test_readers/
│   │   ├── test_wfdb_reader.py
│   │   ├── test_csv_reader.py
│   │   ├── test_mat_reader.py
│   │   └── test_hdf5_reader.py
│   ├── test_resampler.py
│   ├── test_registrar.py
│   └── fixtures/                      # Small sample files for testing
│       ├── sample.hea
│       ├── sample.dat
│       ├── sample.atr
│       └── sample.csv
│
└── scripts/
    ├── init_db.py                     # Run schema.sql against Postgres
    └── validate_manifest.py           # Validate manifest YAML schema
```

---

## 10. Python Dependencies

```
# requirements.txt

apache-airflow==2.8.*
pyspark==3.5.*
boto3>=1.34
botocore>=1.34
wfdb>=4.1
scipy>=1.12
numpy>=1.26
pandas>=2.1
pyarrow>=15.0
h5py>=3.10
psycopg2-binary>=2.9
pyyaml>=6.0
requests>=2.31
```

---

## 11. Implementation Order

Build and test in this order. Each phase should be verified before moving to the next.

### Phase 1 — Foundation (Week 1)
1. Set up Docker Compose with Airflow + PostgreSQL + MinIO
2. Implement `config.py` (loads `pipeline_config.yaml` and `.env`)
3. Implement `s3.py` (S3Client wrapper using boto3)
4. Implement `manifest.py` (DatasetManifest dataclass and YAML loader)
5. Implement `helpers.py` (port `slugify`, `human_size` from existing script)
6. Run `schema.sql` to create PostgreSQL tables
7. **Verify**: Airflow UI accessible at :8080, MinIO console at :9001, Postgres connectable

### Phase 2 — Module 1: Downloader (Week 1–2)
1. Implement `BaseDownloadStrategy` ABC
2. Refactor existing `fetch_physionet` into `PhysioNetS3Strategy` class (write to S3 instead of local disk)
3. Refactor `fetch_zenodo` into `ZenodoStrategy`
4. Refactor `fetch_figshare` into `FigshareStrategy`
5. Refactor `fetch_direct` into `DirectURLStrategy`
6. Implement main `Downloader` class with provider dispatch and idempotency check
7. Create manifest YAML for MIT-BIH Atrial Fibrillation Database
8. **Test**: download AFDB to MinIO, verify files visible in MinIO console

### Phase 3 — Module 2: Reader & Processor (Week 2–3)
1. Implement `ECGRecording` and related data models in `models.py`
2. Implement `WFDBReader` first (handles `.hea`, `.dat`, `.atr`, `.qrs` — covers most datasets)
3. Implement `resample_signal()` and `adjust_annotations()` in `resampler.py`
4. Implement Parquet writer and JSON annotation writer in `writer.py`
5. Implement `FormatDispatcher` in `dispatcher.py`
6. Implement `process_recording()` in `processor.py` — the full per-recording pipeline
7. **Test**: process MIT-BIH AF Database → verify Parquet files in MinIO at 250 Hz with correct metadata and annotation JSONs
8. Add `CSVReader`, `MATReader`, `HDF5Reader` as needed for non-PhysioNet datasets

### Phase 4 — Module 3: Registrar (Week 3)
1. Implement `MetadataRegistrar` with UPSERT logic
2. **Test**: register MIT-BIH AF Database → query Postgres to verify dataset and recording rows
3. Validate example queries from Section 7.3

### Phase 5 — Airflow Integration (Week 3–4)
1. Wire modules into the per-dataset DAG (`ecg_pipeline_dag.py`)
2. **Test**: full end-to-end pipeline for MIT-BIH AF Database triggered from Airflow UI
3. Create batch DAG (`ecg_batch_pipeline_dag.py`)
4. Create resample DAG (`ecg_resample_dag.py`)
5. Create all 21 manifest YAML files (one per dataset in the registry)
6. Run batch processing for all datasets

### Phase 6 — PySpark Parallelism (Week 4–5)
1. Refactor Module 2's dataset-level processing to optionally run via PySpark
2. Add logic: if recording count >= `min_recordings_for_spark`, use Spark; otherwise use Python loop
3. **Test** with a larger dataset (e.g., CPSC 2018 or PTB-XL)
4. Tune Spark memory and parallelism settings in `pipeline_config.yaml`

---

## 12. Key Decisions Summary

1. **No intermediate storage.** Raw files are read, resampled, and written to processed Parquet in a single step. Two S3 prefixes total: `raw/` and `processed/`.

2. **Single default target rate (250 Hz).** Configured in `pipeline_config.yaml`. Additional rates produced on-demand via the resample DAG without re-processing raw files.

3. **All channels preserved.** The pipeline stores every channel from the source. Lead selection happens at training-time data loading (cheap operation — just column selection from Parquet).

4. **Splits deferred to experiment code.** The pipeline ingests all recordings uniformly with `split = NULL`. Experiment code assigns train/val/test splits later via SQL UPDATE, using the `af_present` and `geographic_origin` fields to ensure balanced stratification. This avoids the complexity of parsing heterogeneous split encodings from different datasets and guarantees consistent split methodology across all data.

5. **Retries handled by Airflow.** No custom retry logic in module code. Modules raise exceptions on failure; Airflow retries tasks per `pipeline_config.yaml` settings.

6. **One Parquet file per recording.** Not chunked. Even a 24-hour Holter at 250 Hz with 12 leads is manageable as a single file.

7. **Annotations separate from signal.** JSON sidecar files allow updating annotations independently (e.g., adding model predictions later) without rewriting signal data.

8. **Idempotency via existence checks.** Each module checks if its output already exists before processing. Safe to re-run.

9. **PySpark only for large datasets.** Small datasets (< 50 recordings) use a simple Python loop. Threshold configurable in `pipeline_config.yaml`.

10. **Config over code.** All tunable parameters live in `pipeline_config.yaml` (pipeline behavior) or `.env` (deployment settings). Nothing hardcoded.

11. **Five download providers matching existing script.** PhysioNet S3, Zenodo, Figshare, Dataverse, and direct URL — same providers and API logic as the current `fetch_datasets.py`, refactored into strategy classes that write to S3.

12. **Provider inferrable from URL.** The `source.provider` manifest field is explicit for documentation, but can be auto-detected from the URL using the same dispatch logic as the existing script.

13. **Three-state `af_present` semantics.** `TRUE` means AF was found in rhythm annotations; `FALSE` means rhythm annotations exist but contain no AF; `NULL` means no rhythm annotations are available (AF status unknown). This prevents unannotated recordings from being incorrectly treated as AF-negative.
