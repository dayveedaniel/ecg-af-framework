"""Inventory or export the processed layer as a portable Hugging Face dataset.

Run with the data-pipeline environment. Export never uploads anything; use the
Hub CLI on the completed directory, as documented in docs/PUBLISHING.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
import pyarrow.parquet as pq
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ecg_pipeline.utils.env import load_dotenv_file

ROOT = Path(__file__).resolve().parents[1]
LICENSES = {
    "ODC-By 1.0": ("odc-by", "https://opendatacommons.org/licenses/by/1-0/"),
    "CC BY 4.0": ("cc-by-4.0", "https://creativecommons.org/licenses/by/4.0/"),
}


def relative_key(uri: str, bucket: str, prefix: str) -> str:
    parsed = urlsplit(uri)
    key = parsed.path.lstrip("/")
    if parsed.scheme != "s3" or parsed.netloc != bucket or not key.startswith(prefix):
        raise ValueError(f"Recording points outside selected dataset: {uri}")
    relative = key[len(prefix):]
    parts = PurePosixPath(relative).parts
    if not parts or any(p in {".", ".."} for p in relative.split("/")) or "\\" in relative:
        raise ValueError(f"Unsafe object path: {uri}")
    return relative


def inventory(client, bucket: str, prefix: str) -> dict:
    objects = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            objects[obj["Key"]] = obj
    return objects


def hub_relative_path(relative: str) -> str:
    # The Hub rejects path components beginning x00 (e.g. Leipzig x001).
    # Preserve recording IDs and file bytes while making the storage path portable.
    return "/".join("recording-" + part if part.startswith("x00") else part
                    for part in PurePosixPath(relative).parts)


def download(client, bucket: str, key: str, destination: Path, obj: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # IfMatch rejects a source object changed since inventory was taken.
    response = client.get_object(Bucket=bucket, Key=key, IfMatch=obj["ETag"])
    with response["Body"] as stream, destination.open("wb") as target:
        shutil.copyfileobj(stream, target)
    if destination.stat().st_size != obj["Size"]:
        raise ValueError(f"Incomplete download: {key}")


def export_subset(client, bucket: str, prefix: str, objects: dict, manifest: dict, folder: Path) -> dict:
    name = manifest["dataset"]["name"]
    report_key = prefix + "_processing_report.json"
    if report_key not in objects:
        raise ValueError(f"Missing processing report: {report_key}")
    response = client.get_object(Bucket=bucket, Key=report_key, IfMatch=objects[report_key]["ETag"])
    with response["Body"] as body:
        report = json.load(body)
    rate = int(prefix.split("/")[1].removesuffix("hz"))
    if report.get("dataset") != name or report.get("target_sampling_rate") != rate:
        raise ValueError(f"Processing report identity/rate mismatch: {name}")
    results = report.get("results", [])
    if not results or len(results) != report["summary"]["ok_recordings"]:
        raise ValueError(f"Empty or inconsistent processing report: {name}")
    referenced = set()
    rows = []
    ids = set()
    for source in results:
        row = dict(source)
        recording_id = row["full_recording_id"]
        if recording_id in ids:
            raise ValueError(f"Duplicate recording: {recording_id}")
        ids.add(recording_id)
        row["dataset_name"] = name
        row["source_license"] = manifest["metadata"]["license"]
        for field in ("parquet_path", "annotations_path"):
            uri = row.get(field)
            if not uri:
                continue
            relative = relative_key(uri, bucket, prefix)
            key = prefix + relative
            suffix = ".parquet" if field == "parquet_path" else ".annotations.json"
            if not relative.endswith(suffix) or key not in objects or key in referenced:
                raise ValueError(f"Missing, duplicate, or invalid recording file: {uri}")
            referenced.add(key)
            local = f"processed/{rate}hz/{name}/{hub_relative_path(relative)}"
            download(client, bucket, key, folder / local, objects[key])
            row[field] = local
        parquet = pq.ParquetFile(folder / row["parquet_path"])
        metadata = parquet.schema_arrow.metadata or {}
        if parquet.metadata.num_rows != row["num_samples"] or row["sampling_rate"] != rate:
            raise ValueError(f"Sample count/rate mismatch: {recording_id}")
        if metadata.get(b"dataset_name", b"").decode() != name:
            raise ValueError(f"Parquet dataset mismatch: {recording_id}")
        if row.get("annotations_path"):
            annotations = json.loads((folder / row["annotations_path"]).read_text())
            if annotations.get("target_sampling_rate") != rate:
                raise ValueError(f"Annotation sampling rate mismatch: {recording_id}")
            if annotations.get("recording_id") != metadata.get(b"recording_id", b"").decode():
                raise ValueError(f"Annotation recording mismatch: {recording_id}")
        rows.append(row)
    # Reject stale/unregistered waveform or annotation objects; do not silently omit them.
    actual = {k for k in objects if k.endswith((".parquet", ".annotations.json"))}
    if referenced != actual:
        raise ValueError(f"Processing report does not cover all processed files: {name}")
    index = folder / "metadata" / f"{name}.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    provenance = folder / "provenance"
    provenance.mkdir(exist_ok=True)
    (provenance / f"{name}.json").write_text(json.dumps({
        "manifest": manifest,
        "processing_summary": report["summary"],
        "recordings_exported": len(rows),
    }, indent=2, ensure_ascii=False) + "\n")
    return {"name": name, "recordings": len(rows), "bytes": sum(objects[k]["Size"] for k in referenced)}


def write_card(
    folder: Path,
    manifests: list[dict],
    summaries: list[dict],
    repo_id: str,
    framework_ref: str = "main",
) -> None:
    license_ids = sorted({LICENSES[m["metadata"]["license"]][0] for m in manifests})
    header = {
        "license": license_ids,
        "language": ["en"],
        "tags": ["ecg", "atrial-fibrillation", "time-series", "benchmark"],
        "configs": [{"config_name": s["name"], "data_files": [
            {"split": "recordings", "path": f"metadata/{s['name']}.jsonl"}
        ]} for s in summaries],
    }
    citations = []
    summary_rows = []
    for m, s in zip(manifests, summaries):
        license_name = m["metadata"]["license"]
        summary_rows.append(
            f"| [{m['dataset']['display_name']}]({m['source']['url']}) "
            f"| {s['recordings']:,} | {s['bytes'] / 1_000_000_000:.2f} GB | {license_name} |"
        )
        citations.append(
            f"### {m['dataset']['display_name']}\n\n"
            f"- Source: {m['source']['url']} (version {m['dataset']['version']})\n"
            f"- License: [{license_name}]({LICENSES[license_name][1]})\n"
            f"- Exported recordings: {s['recordings']}\n"
            f"- Citation: {m['metadata']['citation']}\n"
        )
    total_recordings = sum(s["recordings"] for s in summaries)
    total_bytes = sum(s["bytes"] for s in summaries)
    recording_word = "recording" if total_recordings == 1 else "recordings"
    summary_table = "\n".join([
        "| Source subset | Recordings | Processed payload | Source license |",
        "| --- | ---: | ---: | --- |",
        *summary_rows,
        f"| **Total** | **{total_recordings:,}** | **{total_bytes / 1_000_000_000:.2f} GB** | Mixed; see each source |",
    ])
    card = "---\n" + yaml.safe_dump(header, sort_keys=False) + "---\n\n"
    card += f"""# ECG-AF processed ECG collection

## What this dataset is

This is a **derived, harmonized release produced by the
[ECG-AF framework](https://github.com/dayveedaniel/ecg-af-framework/tree/{framework_ref})**. The
ECG-AF project did not collect new patient recordings. It downloaded the public
source datasets listed below, converted their heterogeneous signal and
annotation formats into one representation, and exported the framework's
processed layer to Hugging Face.

This release contains **{total_recordings:,} processed ECG {recording_word} at 250 Hz**
with their available annotations, recording-level metadata, source provenance,
and integrity checksums. The exact framework commit is recorded in
`release.json`; this card links the same source revision above.

{summary_table}

Credit for the original recordings and annotations belongs to their respective
creators. Each subset retains its source license; the license list in the card
metadata is not a choice of license for every file.

## How ECG-AF derived this release

```text
Public source archive
  -> ECG-AF provider downloader and format reader
  -> source-specific annotation adapter
  -> signal resampling to 250 Hz (no amplitude normalization)
  -> one signal Parquet + optional annotation JSON per recording
  -> recording index, provenance manifest, and SHA-256 checksums
```

The framework preserves source recording/subject identifiers and records the
original sampling rate, channel names, units, source format, and annotation
availability. It does not add model predictions or impose benchmark labels or
train/validation/test splits.

## Data layout and use

The Dataset Viewer and `datasets.load_dataset` expose **one metadata row per
recording** from each subset's JSONL index:

```python
from datasets import load_dataset

records = load_dataset(
    "OWNER/REPO",
    "cpsc-2021-paroxysmal-af-event-detection-dataset",
    split="recordings",
)
print(records[0]["parquet_path"])
```

`parquet_path` and `annotations_path` are paths relative to this repository, not
external S3 addresses. Download the actual waveforms and annotations with
`huggingface_hub.snapshot_download`:

```python
import json
from pathlib import Path
import pyarrow.parquet as pq
from huggingface_hub import snapshot_download

root = Path(snapshot_download(repo_id="OWNER/REPO", repo_type="dataset"))
index = next((root / "metadata").glob("*.jsonl"))
recording = json.loads(index.read_text().splitlines()[0])
signal = pq.read_table(root / recording["parquet_path"])
annotations = (
    json.loads((root / recording["annotations_path"]).read_text())
    if recording.get("annotations_path") else None
)
```

Signal Parquet files are preserved byte for byte: one sample per row, a
`sample_index` column, and one `channel_<index>_<lead>` column per lead.
Footer metadata records sampling rates, channels, units, source format, and
original filename. JSON sidecars contain beat positions/labels, rhythm episodes,
delineation, and dataset-specific labels when available. Annotation sample
positions refer to the processed sampling rate. Signals are resampled by the
pipeline; its current default does not normalize amplitudes. Consult each
recording's units and source documentation rather than assuming uniform units.

`metadata/` retains recording and subject identifiers, sampling rates, channels,
durations, annotation availability, and source licenses from processing reports.
`provenance/` contains source manifests and processing counts. `release.json`
records the code revision and exported counts. `SHA256SUMS` covers release files.
Raw archives, model outputs, database dumps, and credentials are not included.
Storage filenames beginning `x00` receive a `recording-` prefix for Hub path
compatibility; recording identifiers and file contents are preserved.

## Labels, splitting, and limitations

No training/validation/test split is imposed. The `recordings` split is an inventory,
not a training recommendation. Group by dataset and subject when subject IDs
exist, and by recording otherwise. Keep slices of a recording together and
check source-specific identity conventions before evaluating models.

AF-vs-NSR window labels are a downstream benchmark policy, not the native class
of every source. Missing annotations are unknown; do not interpret them as NSR.
The AF Termination Challenge subset consists of AF examples and does not supply
a normal control group. Populations, lead configurations, units, and annotation
quality vary across sources. The AF Termination subset's full-record AF rhythm
spans are generated by the adapter from challenge membership; its beat
annotations come from the original WFDB QRS files. Consult annotation sidecar
provenance fields when distinguishing derived and original labels. Inspect source
documentation and processing summaries when interpreting cross-dataset results.

This release preserves the processed source content and identifiers; it does
not perform an additional de-identification pass. It is a research dataset and
has not been clinically validated by this project.

## Sources, attribution, and licenses

"""
    card += "\n".join(citations)
    (folder / "README.md").write_text(card.replace("OWNER/REPO", repo_id))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--bucket", default="ecg-pipeline")
    parser.add_argument("--rate", type=int, default=250)
    parser.add_argument("--dataset", action="append", help="Exact manifest dataset name; repeat to select subsets.")
    parser.add_argument("--output", type=Path, help="New export directory. Omit for inventory only.")
    parser.add_argument("--repo-id", default="OWNER/REPO", help="Hugging Face owner/name for the card example.")
    parser.add_argument(
        "--framework-ref",
        default="main",
        help="ECG-AF Git commit or tag used to derive the release; linked from the dataset card.",
    )
    args = parser.parse_args(argv)
    if args.rate <= 0:
        parser.error("--rate must be positive")
    manifests = [yaml.safe_load(p.read_text()) for p in sorted((ROOT / "manifests").glob("*.yaml"))]
    names = {m["dataset"]["name"] for m in manifests}
    if args.dataset and set(args.dataset) - names:
        parser.error(f"Unknown datasets: {sorted(set(args.dataset) - names)}")
    manifests = [m for m in manifests if not args.dataset or m["dataset"]["name"] in args.dataset]
    env = {**load_dotenv_file(args.env_file), **os.environ}
    client = boto3.client(
        "s3", endpoint_url=env["S3_ENDPOINT_URL"],
        aws_access_key_id=env["S3_ACCESS_KEY"], aws_secret_access_key=env["S3_SECRET_KEY"],
        config=Config(s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=120,
                      retries={"max_attempts": 2}),
    )
    selections = []
    for manifest in manifests:
        name = manifest["dataset"]["name"]
        prefix = f"processed/{args.rate}hz/{name}/"
        objects = inventory(client, args.bucket, prefix)
        print(json.dumps({"dataset": name, "objects": len(objects),
                          "bytes": sum(o["Size"] for o in objects.values())}), flush=True)
        selections.append((manifest, prefix, objects))
    if args.output is None:
        return 0
    output = args.output.resolve()
    if output.exists():
        parser.error("--output must not exist; use a new directory to avoid stale release files")
    if any(not objects for _, _, objects in selections):
        parser.error("Selected datasets have no processed objects; select available subsets with --dataset")
    if any(m["metadata"].get("license") not in LICENSES for m in manifests):
        parser.error("Every selected source must have a verified license")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ecg-export-", dir=output.parent) as temp:
        folder = Path(temp)
        summaries = [export_subset(client, args.bucket, prefix, objects, m, folder)
                     for m, prefix, objects in selections]
        write_card(folder, manifests, summaries, args.repo_id, args.framework_ref)
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    capture_output=True, text=True, check=True).stdout.strip())
        (folder / "release.json").write_text(json.dumps({
            "format_version": 1, "git_revision": revision, "working_tree_dirty": dirty,
            "sampling_rate": args.rate, "datasets": summaries,
        }, indent=2) + "\n")
        checksums = []
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                checksums.append(f"{digest}  {path.relative_to(folder).as_posix()}\n")
        (folder / "SHA256SUMS").write_text("".join(checksums))
        shutil.move(str(folder), output)
    print(f"Export complete: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
