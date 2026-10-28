# Publishing ECG-AF

Use three linked deliverables: a Hugging Face **dataset** repository for processed
data, a GitHub source release for the framework, and a Docker image for running
the framework. The public source release is published separately at
`https://github.com/dayveedaniel/ecg-af-framework`. The original research
workspace remote is `https://github.com/itdusty/ECG-AF.git`.
The image does not embed the dataset or local credentials.

## Dataset export

Start the existing MinIO service containing the collected data. Do not create a
fresh empty volume and assume it contains the previous collection. From the
repository root, inventory actual processed objects and their sizes:

```bash
uv run --project data-pipeline python data-pipeline/scripts/export_huggingface.py
```

The six manifests describe source datasets; inventory determines which are
actually available. Export all available sources, or repeat `--dataset NAME` to
select specific sources. A new output directory is required:

```bash
uv run --project data-pipeline python data-pipeline/scripts/export_huggingface.py \
  --dataset af-termination-challenge-database \
  --dataset arrhythmias-in-children-and-patients-with-congenital-heart-disease \
  --dataset cpsc-2021-paroxysmal-af-event-detection-dataset \
  --dataset mit-bih-atrial-fibrillation-database \
  --repo-id dayveedd/ecg-af-250hz --output releases/ecg-af-250hz-v0.1.0
```

This first release contains the four sources above (1,567 recordings). IRIDIA-AF
and NCKU CBIC have manifests but were not present in the collected processed
layer. The default source is `data-pipeline/.env`, bucket `ecg-pipeline`, and processed
rate 250 Hz. Override with `--env-file`, `--bucket`, or `--rate` as needed.
Environment variables override file values. No PostgreSQL connection is needed:
the processing reports carry recording metadata and subject identifiers.

The exporter validates processing-report coverage, recording IDs, Parquet sample
counts, and annotation sampling rates; downloads the original processed bytes;
rewrites storage pointers as relative paths; and generates a recording index,
source provenance, dataset card, and SHA-256 checksums. It exports neither raw
archives nor benchmark outputs. Failed exports do not leave a completed output
directory. No training split is invented. Review the generated card, exported
identifiers, source attribution, and processing counts before uploading.

The Hugging Face Viewer displays recording metadata. Waveforms and annotation
sidecars are downloadable files; the card includes a `snapshot_download` example.
This avoids turning each signal sample into a separate training example or
mixing variable lead schemas into a single table. The benchmark currently reads
MinIO/S3 and PostgreSQL, so uploading to Hugging Face does not automatically make
the framework consume a Hub repository. A future importer should restore the
relative object layout and register recordings from the exported index.

## Upload to Hugging Face

Authenticate locally; do not put tokens in the repository or chat:

```bash
uvx --from huggingface_hub hf auth login
```

Replace `OWNER` with your Hugging Face username or organization. Create a dataset
repository, then use the resumable uploader:

```bash
uvx --from huggingface_hub hf repos create OWNER/ecg-af-250hz --repo-type dataset
uvx --from huggingface_hub hf upload OWNER/ecg-af-250hz \
  releases/ecg-af-250hz-v0.1.0 --repo-type dataset
```

Repository creation is public by default. For staging, add `--private` to the
create command and change visibility in repository settings after validation.
Use a new repository for the first release; the uploader does not
remove stale files from previous releases. After uploading, download into a
separate directory and verify `SHA256SUMS`, the recording counts, sample loading,
and Viewer configurations before announcing the release. Record the final Hub
revision in the GitHub release notes.

Official references: [uploading](https://huggingface.co/docs/huggingface_hub/guides/upload),
[dataset cards](https://huggingface.co/docs/hub/datasets-cards), and
[Viewer configuration](https://huggingface.co/docs/hub/datasets-manual-configuration).

## Source licenses

The following licenses were checked against upstream pages/API metadata while
preparing publication. Preserve each source's attribution and license when
redistributing its processed subset; this collection does not replace them with
one new license.

| Subset | Source license | Source |
| --- | --- | --- |
| MIT-BIH AFDB | ODC-By 1.0 | [PhysioNet](https://physionet.org/content/afdb/1.0.0/) |
| AF Termination Challenge | ODC-By 1.0 | [PhysioNet](https://physionet.org/content/aftdb/1.0.0/) |
| Leipzig Heart Center | ODC-By 1.0 | [PhysioNet](https://physionet.org/content/leipzig-heart-center-ecg/1.0.0/) |
| CPSC 2021 | CC BY 4.0 | [PhysioNet](https://physionet.org/content/cpsc2021/1.0.0/) |
| IRIDIA-AF | CC BY 4.0 | [Zenodo API metadata](https://zenodo.org/api/records/8405941) |
| NCKU CBIC | CC BY 4.0 | [Figshare](https://figshare.com/articles/dataset/NCKU_CBIC_ECG_Database/23807286) |

The framework **code** is licensed under MIT, declared in the repository LICENSE
and both Python packages. Dataset licenses and the framework code license are
separate; MIT does not relicense third-party recordings or annotations.

## Docker framework

Build from the repository root. The default includes CPU PyTorch, Java 17,
Spark, the benchmark CLI, dashboard assets, and ingestion tools:

```bash
docker build -f Dockerfile.benchmark -t ecg-af-benchmark:0.1.1 .
docker run --rm ecg-af-benchmark:0.1.1 --help
docker run --rm ecg-af-benchmark:0.1.1 validate-config \
  --config /app/py_ecg_benchmark_pkg/examples/benchmark.yaml --skip-env
```

For a smaller image without optional waveform models, build with
`--build-arg INSTALL_TORCH=false`. Optional XGBoost is not included; the supplied
Goodfellow model defaults to its scikit-learn backend.

Run the dashboard against the existing local services and volumes:

```bash
docker compose --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f data-pipeline/docker-compose.benchmark.yaml up -d --build benchmark
```

Open `http://127.0.0.1:8765`. The dashboard runs jobs inside the benchmark
container. MinIO retains artifacts and caches; generated local job files are
ephemeral. Stop with the same Compose files and `stop benchmark`.

Run a benchmark directly on the same Compose network:

```bash
docker compose --env-file data-pipeline/.env.docker \
  -f data-pipeline/docker-compose.yaml \
  -f data-pipeline/docker-compose.benchmark.yaml run --rm benchmark run \
  --config /app/py_ecg_benchmark_pkg/examples/showcase_smoke.yaml
```

For Docker Hub publication, replace `DOCKER_OWNER` with your namespace:

```bash
docker login
docker tag ecg-af-benchmark:0.1.1 DOCKER_OWNER/ecg-af-benchmark:0.1.1
docker push DOCKER_OWNER/ecg-af-benchmark:0.1.1
```

Alternatively use `ghcr.io/dayveedaniel/ecg-af-benchmark:0.1.1` on GitHub Container
Registry. Push only after the image builds and the smoke commands pass.
This Dockerfile is a release starting point, not a claim of bit-for-bit
reproducibility: Python packages use the project's version ranges and the base
image uses a version tag. Pin a tested dependency set and base digest for stricter
reproducibility.

## GitHub release

The public release is a clean snapshot of the framework, dashboard, pipeline,
and examples, without the private research repository's Git history or thesis
drafts. Before tagging:

1. Check the MIT license notices and author/citation metadata.
2. Exclude generated dashboard jobs/logs and saved notebook outputs from the
   release snapshot. `.gitignore` and `.dockerignore` do not remove tracked files.
3. Build a wheel and source distribution of `py_ecg_benchmark_pkg`; run its
   tests and the container smoke commands above.
4. Publish the dataset and record its Hub repository and immutable revision.
5. Commit the reviewed changes, tag `v0.1.1`, and create a GitHub Release with
   the wheel/sdist, tested image tag/digest, and dataset link.

The benchmark wheel exposes the CLI and Python API. The dashboard currently
depends on the sibling `dashboard/` directory and is not a standalone wheel
feature; the Docker image and full GitHub checkout include it.
