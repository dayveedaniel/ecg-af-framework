from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from psycopg2.extensions import connection as PGConnection

from ecg_pipeline.manifest import DatasetManifest
from ecg_pipeline.reader.models import ProcessingResult


class MetadataRegistrar:
    def __init__(
        self,
        db_connection: PGConnection,
        *,
        pipeline_version: str = "0.1.0",
        ensure_schema: bool = True,
    ) -> None:
        self._conn = db_connection
        self._pipeline_version = pipeline_version
        if ensure_schema:
            self.ensure_schema()

    def ensure_schema(self) -> None:
        schema_sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self._conn.cursor() as cur:
            cur.execute(schema_sql)
        self._conn.commit()

    def register_dataset(self, manifest: DatasetManifest) -> int:
        annotations = set(manifest.annotations.types or [])
        has_beat = "beat_labels" in annotations
        has_rhythm = "rhythm_labels" in annotations
        has_delineation = "delineation" in annotations

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO datasets (
                    name, display_name, description, source_url, license, citation,
                    geographic_origin, num_subjects, num_recordings,
                    has_beat_annotations, has_rhythm_annotations, has_delineation_annotations,
                    pipeline_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE
                SET
                    display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    source_url = EXCLUDED.source_url,
                    license = EXCLUDED.license,
                    citation = EXCLUDED.citation,
                    geographic_origin = EXCLUDED.geographic_origin,
                    num_subjects = EXCLUDED.num_subjects,
                    num_recordings = EXCLUDED.num_recordings,
                    has_beat_annotations = EXCLUDED.has_beat_annotations,
                    has_rhythm_annotations = EXCLUDED.has_rhythm_annotations,
                    has_delineation_annotations = EXCLUDED.has_delineation_annotations,
                    pipeline_version = EXCLUDED.pipeline_version
                RETURNING dataset_id
                """,
                (
                    manifest.dataset.name,
                    manifest.dataset.display_name,
                    manifest.metadata.description,
                    manifest.source.url,
                    manifest.metadata.license,
                    manifest.metadata.citation,
                    manifest.metadata.geographic_origin,
                    manifest.metadata.num_subjects,
                    manifest.metadata.num_recordings,
                    has_beat,
                    has_rhythm,
                    has_delineation,
                    self._pipeline_version,
                ),
            )
            dataset_id = int(cur.fetchone()[0])
        self._conn.commit()
        return dataset_id

    def register_recordings(self, dataset_name: str, processing_results: list[ProcessingResult]) -> int:
        if not processing_results:
            return 0

        dataset_id = self._get_dataset_id(dataset_name)
        rows = [self._processing_result_to_row(dataset_id, item) for item in processing_results]

        with self._conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    """
                    INSERT INTO recordings (
                        dataset_id, recording_name, full_recording_id,
                        sampling_rate, original_sampling_rate, num_channels, channel_names,
                        duration_seconds, num_samples, source_format,
                        subject_id, age, sex,
                        has_beat_annotations, has_rhythm_annotations, has_delineation_annotations,
                        num_beat_annotations, num_rhythm_episodes, af_present,
                        parquet_path, annotations_path, pipeline_version
                    )
                    VALUES (
                        %(dataset_id)s, %(recording_name)s, %(full_recording_id)s,
                        %(sampling_rate)s, %(original_sampling_rate)s, %(num_channels)s, %(channel_names)s,
                        %(duration_seconds)s, %(num_samples)s, %(source_format)s,
                        %(subject_id)s, %(age)s, %(sex)s,
                        %(has_beat_annotations)s, %(has_rhythm_annotations)s, %(has_delineation_annotations)s,
                        %(num_beat_annotations)s, %(num_rhythm_episodes)s, %(af_present)s,
                        %(parquet_path)s, %(annotations_path)s, %(pipeline_version)s
                    )
                    ON CONFLICT (full_recording_id) DO UPDATE
                    SET
                        dataset_id = EXCLUDED.dataset_id,
                        recording_name = EXCLUDED.recording_name,
                        sampling_rate = EXCLUDED.sampling_rate,
                        original_sampling_rate = EXCLUDED.original_sampling_rate,
                        num_channels = EXCLUDED.num_channels,
                        channel_names = EXCLUDED.channel_names,
                        duration_seconds = EXCLUDED.duration_seconds,
                        num_samples = EXCLUDED.num_samples,
                        source_format = EXCLUDED.source_format,
                        subject_id = EXCLUDED.subject_id,
                        age = EXCLUDED.age,
                        sex = EXCLUDED.sex,
                        has_beat_annotations = EXCLUDED.has_beat_annotations,
                        has_rhythm_annotations = EXCLUDED.has_rhythm_annotations,
                        has_delineation_annotations = EXCLUDED.has_delineation_annotations,
                        num_beat_annotations = EXCLUDED.num_beat_annotations,
                        num_rhythm_episodes = EXCLUDED.num_rhythm_episodes,
                        af_present = EXCLUDED.af_present,
                        parquet_path = EXCLUDED.parquet_path,
                        annotations_path = EXCLUDED.annotations_path,
                        pipeline_version = EXCLUDED.pipeline_version
                    """,
                    row,
                )

        self._refresh_dataset_aggregates(dataset_id=dataset_id)
        self._conn.commit()
        return len(rows)

    def register_resampled_variants(
        self,
        dataset_name: str,
        target_rate: int,
        processing_results: list[ProcessingResult] | None = None,
    ) -> int:
        if not processing_results:
            return 0

        self._get_dataset_id(dataset_name)
        written = 0
        with self._conn.cursor() as cur:
            for item in processing_results:
                if item.sampling_rate != target_rate:
                    continue
                cur.execute(
                    """
                    INSERT INTO resampled_recordings (
                        recording_id, target_sampling_rate, parquet_path, annotations_path
                    )
                    SELECT recording_id, %s, %s, %s
                    FROM recordings
                    WHERE full_recording_id = %s
                    ON CONFLICT (recording_id, target_sampling_rate) DO UPDATE
                    SET parquet_path = EXCLUDED.parquet_path,
                        annotations_path = EXCLUDED.annotations_path
                    """,
                    (target_rate, item.parquet_path, item.annotations_path, item.full_recording_id),
                )
                written += 1
        self._conn.commit()
        return written

    def _get_dataset_id(self, dataset_name: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT dataset_id FROM datasets WHERE name = %s", (dataset_name,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"Dataset '{dataset_name}' is not registered. Call register_dataset first.")
        return int(row[0])

    def _processing_result_to_row(self, dataset_id: int, item: ProcessingResult) -> dict[str, Any]:
        row = asdict(item)
        row["dataset_id"] = dataset_id
        row["pipeline_version"] = self._pipeline_version
        row["channel_names"] = row["channel_names"]
        return row

    def _refresh_dataset_aggregates(self, *, dataset_id: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE datasets
                SET
                    num_recordings = stats.num_recordings,
                    has_beat_annotations = stats.has_beat_annotations,
                    has_rhythm_annotations = stats.has_rhythm_annotations,
                    has_delineation_annotations = stats.has_delineation_annotations
                FROM (
                    SELECT
                        dataset_id,
                        COUNT(*)::INTEGER AS num_recordings,
                        BOOL_OR(has_beat_annotations) AS has_beat_annotations,
                        BOOL_OR(has_rhythm_annotations) AS has_rhythm_annotations,
                        BOOL_OR(has_delineation_annotations) AS has_delineation_annotations
                    FROM recordings
                    WHERE dataset_id = %s
                    GROUP BY dataset_id
                ) stats
                WHERE datasets.dataset_id = stats.dataset_id
                """,
                (dataset_id,),
            )
