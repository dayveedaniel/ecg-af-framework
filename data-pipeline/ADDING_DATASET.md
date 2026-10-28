# Adding a New Dataset

This guide describes how to add a dataset end-to-end in this repository.

## Overview

For every new dataset you should add:
1. A manifest YAML in `data-pipeline/manifests/`.
2. A dataset annotation adapter in `data-pipeline/src/ecg_pipeline/annotations/adapters/`.
3. An adapter registration import in `data-pipeline/src/ecg_pipeline/annotations/adapters/__init__.py`.

The pipeline is now adapter-based. A manifest must include `annotations.adapter`.

## Step 1: Create the manifest

Create a file:
- `data-pipeline/manifests/<dataset-name>.yaml`

Use this template:

```yaml
dataset:
  name: "my-dataset"
  display_name: "My Dataset"
  version: "1.0.0"

source:
  url: "https://..."
  provider: "zenodo"  # one of: physionet_s3 | zenodo | figshare | direct_url

force_redownload: false

metadata:
  description: "..."
  citation: "..."
  license: null
  geographic_origin: null
  num_subjects: null
  num_recordings: null
  leads: null

annotations:
  adapter: "my_dataset_v1"
  options: {}
  available: false
  types: []

quirks: []
```

Notes:
1. `annotations.adapter` is required.
2. `annotations.options` is optional but recommended for adapter configuration.
3. `available` and `types` are metadata fields and do not control parsing behavior.

## Step 2: Create the adapter

Create a file:
- `data-pipeline/src/ecg_pipeline/annotations/adapters/my_dataset.py`

Minimal adapter skeleton:

```python
from __future__ import annotations

from typing import Any

from ecg_pipeline.annotations.base import AnnotationAdapter
from ecg_pipeline.annotations.context import AnnotationContext
from ecg_pipeline.annotations.registry import register_annotation_adapter
from ecg_pipeline.reader.models import ECGRecording, RecordingFileGroup


@register_annotation_adapter("my_dataset_v1")
class MyDatasetAdapter(AnnotationAdapter):
    def __init__(self, *, options: dict[str, Any] | None = None) -> None:
        super().__init__(options=options)
        self._sidecars: set[str] = set()

    def prepare(self, context: AnnotationContext) -> None:
        # Optional: preload sidecar CSV/JSON files once.
        # Example:
        # rows = context.read_csv_rows("path/to/metadata.csv")
        # self._sidecars.add("path/to/metadata.csv")
        pass

    def sidecar_files(self, context: AnnotationContext) -> set[str]:
        # Return non-signal files to exclude from waveform discovery.
        return set(self._sidecars)

    def reader_options_for_group(self, *, file_group: RecordingFileGroup) -> dict[str, Any]:
        # Optional: reader tuning (for example HDF5 batch axis mapping).
        return {}

    def enrich_recording(
        self,
        recording: ECGRecording,
        *,
        context: AnnotationContext,
        file_group: RecordingFileGroup,
        recording_index: int,
    ) -> None:
        # Add demographics/annotations/labels onto the single-record object.
        # Examples:
        # recording.age = ...
        # recording.sex = ...
        # recording.subject_id = ...
        # recording.labels = {"label": "AF"}
        pass
```

Adapter design guidance:
1. Keep readers signal-only; do not parse labels in reader classes.
2. Use `prepare(...)` to preload sidecars once per dataset.
3. Use `sidecar_files(...)` to prevent sidecar CSV files from being processed as waveform recordings.
4. Use `recording.slice_index` when mapping sidecar rows to batched HDF5 outputs.

## Step 3: Register the adapter

Edit:
- `data-pipeline/src/ecg_pipeline/annotations/adapters/__init__.py`

Add your import and `__all__` entry.

Example:

```python
from ecg_pipeline.annotations.adapters.my_dataset import MyDatasetAdapter
```

## Step 4: Align manifest and adapter names

Confirm the manifest adapter string exactly matches the decorator string.

Example:
1. Manifest: `annotations.adapter: "my_dataset_v1"`
2. Adapter: `@register_annotation_adapter("my_dataset_v1")`

## Step 5: Validate locally

Validate manifest shape:

```bash
PYTHONPATH=data-pipeline/src python3 data-pipeline/scripts/validate_manifest.py data-pipeline/manifests/my-dataset.yaml
```

Run download-only:

```bash
PYTHONPATH=data-pipeline/src python3 data-pipeline/scripts/download_dataset.py --env-file data-pipeline/.env --manifest data-pipeline/manifests/my-dataset.yaml
```

Run process-only:

```bash
PYTHONPATH=data-pipeline/src python3 data-pipeline/scripts/process_dataset.py --env-file data-pipeline/.env --manifest data-pipeline/manifests/my-dataset.yaml
```

Run end-to-end:

```bash
PYTHONPATH=data-pipeline/src python3 data-pipeline/scripts/run_dataset_pipeline.py --env-file data-pipeline/.env --manifest data-pipeline/manifests/my-dataset.yaml
```

## Step 6: Airflow options

Use one of these DAGs:
1. `ecg_download_dataset`: download/extract/prune only.
2. `ecg_process_register_dataset`: process + register using existing raw data.
3. `ecg_dataset_pipeline`: full flow (download -> process -> register).

## Common pitfalls

1. Missing `annotations.adapter` in manifest: manifest load fails.
2. Adapter not imported in `annotations/adapters/__init__.py`: unknown adapter error.
3. Sidecar files not returned by `sidecar_files(...)`: CSV metadata may be misdetected as signals.
4. Batched tensors not row-aligned with sidecars: wrong labels on output recordings.
5. Adapter name mismatch between manifest and decorator.
