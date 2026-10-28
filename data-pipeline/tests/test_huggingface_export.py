from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_huggingface.py"
spec = importlib.util.spec_from_file_location("export_huggingface", SCRIPT)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_paginator(self, operation):
        return self

    def paginate(self, *, Bucket, Prefix):
        yield {"Contents": [dict(Key=k, Size=len(v), ETag='"etag"')
                            for k, v in self.objects.items() if k.startswith(Prefix)]}

    def get_object(self, *, Bucket, Key, IfMatch):
        assert IfMatch == '"etag"'
        return {"Body": io.BytesIO(self.objects[Key])}


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.name = "mit-bih-atrial-fibrillation-database"
        self.prefix = f"processed/250hz/{self.name}/"
        self.signal = self.prefix + "record.parquet"
        self.annotations = self.prefix + "record.annotations.json"
        table = pa.table({"sample_index": [0, 1], "channel_0_II": [0.1, 0.2]})
        table = table.replace_schema_metadata({b"dataset_name": self.name.encode(), b"recording_id": b"record"})
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        self.report = {
            "dataset": self.name, "target_sampling_rate": 250,
            "summary": {"ok_recordings": 1},
            "results": [{
                "full_recording_id": self.name + "/record", "subject_id": "subject-1",
                "parquet_path": "s3://ecg-pipeline/" + self.signal,
                "annotations_path": "s3://ecg-pipeline/" + self.annotations,
                "num_samples": 2, "sampling_rate": 250,
            }],
        }
        self.objects = {
            self.signal: sink.getvalue().to_pybytes(),
            self.annotations: json.dumps({"recording_id": "record", "target_sampling_rate": 250}).encode(),
        }

    def run_export(self, folder):
        self.objects[self.prefix + "_processing_report.json"] = json.dumps(self.report).encode()
        with patch.object(exporter.boto3, "client", return_value=FakeS3(self.objects)):
            return exporter.main([
                "--env-file", str(exporter.ROOT / ".env.example"),
                "--dataset", self.name, "--output", str(folder),
            ])

    def test_export_preserves_bytes_ids_and_verifiable_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "release"
            self.assertEqual(self.run_export(folder), 0)
            row = json.loads((folder / "metadata" / f"{self.name}.jsonl").read_text())
            self.assertEqual(row["subject_id"], "subject-1")
            self.assertEqual(row["parquet_path"], self.signal)
            self.assertEqual((folder / self.signal).read_bytes(), self.objects[self.signal])
            self.assertNotIn("s3://", (folder / "metadata" / f"{self.name}.jsonl").read_text())
            for line in (folder / "SHA256SUMS").read_text().splitlines():
                digest, relative = line.split("  ", 1)
                self.assertEqual(hashlib.sha256((folder / relative).read_bytes()).hexdigest(), digest)
            card = (folder / "README.md").read_text()
            self.assertIn("ODC-By 1.0", card)
            self.assertIn("split: recordings", card)

    def test_rejects_paths_outside_dataset(self):
        for uri in ["s3://other/" + self.signal,
                    "s3://ecg-pipeline/" + self.prefix + "../outside.parquet",
                    "https://example.com/file.parquet"]:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                exporter.relative_key(uri, "ecg-pipeline", self.prefix)

    def test_hub_compatible_leipzig_paths_preserve_extensions(self):
        self.assertEqual(exporter.hub_relative_path("x001.parquet"), "recording-x001.parquet")
        self.assertEqual(exporter.hub_relative_path("x001.annotations.json"), "recording-x001.annotations.json")
        self.assertEqual(exporter.hub_relative_path("record.parquet"), "record.parquet")

    def test_incomplete_report_does_not_leave_publishable_directory(self):
        self.objects[self.prefix + "stale.parquet"] = b"unregistered"
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "release"
            with self.assertRaisesRegex(ValueError, "does not cover"):
                self.run_export(folder)
            self.assertFalse(folder.exists())

    def test_wrong_annotation_identity_is_rejected(self):
        self.objects[self.annotations] = json.dumps({
            "recording_id": "other", "target_sampling_rate": 250,
        }).encode()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Annotation recording mismatch"):
                self.run_export(Path(temp) / "release")

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            marker = folder / "keep.txt"
            marker.write_text("keep")
            with self.assertRaises(SystemExit):
                self.run_export(folder)
            self.assertEqual(marker.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
