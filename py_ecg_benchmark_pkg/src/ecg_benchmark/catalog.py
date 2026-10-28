from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ecg_benchmark.config import DatabaseConfig
from ecg_benchmark.splits import RecordingRecord


BENCHMARK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id              VARCHAR(255) PRIMARY KEY,
    experiment_name     VARCHAR(255) NOT NULL,
    random_seed         INTEGER NOT NULL,
    config_hash         VARCHAR(64) NOT NULL,
    split_hash          VARCHAR(64),
    artifact_root_uri   VARCHAR(1024) NOT NULL,
    resolved_config_uri VARCHAR(1024),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS benchmark_splits (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              VARCHAR(255) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE,
    dataset_name        VARCHAR(255) NOT NULL,
    full_recording_id   VARCHAR(512) NOT NULL,
    split               VARCHAR(20) NOT NULL,
    group_id            VARCHAR(768) NOT NULL,
    af_present          BOOLEAN,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_id, full_recording_id)
);

CREATE TABLE IF NOT EXISTS benchmark_models (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              VARCHAR(255) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE,
    source_dataset      VARCHAR(255) NOT NULL,
    model_id            VARCHAR(255) NOT NULL,
    artifact_uri        VARCHAR(1024) NOT NULL,
    trained_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(run_id, source_dataset, model_id)
);

CREATE TABLE IF NOT EXISTS benchmark_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              VARCHAR(255) REFERENCES benchmark_runs(run_id) ON DELETE CASCADE,
    source_dataset      VARCHAR(255) NOT NULL,
    target_dataset      VARCHAR(255) NOT NULL,
    model_id            VARCHAR(255) NOT NULL,
    evaluation_level    VARCHAR(32) NOT NULL DEFAULT 'recording',
    metric_name         VARCHAR(255) NOT NULL,
    metric_value        DOUBLE PRECISION,
    artifact_uri        VARCHAR(1024),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT benchmark_metrics_unique_level UNIQUE (
        run_id, source_dataset, target_dataset, model_id, evaluation_level, metric_name
    )
);

ALTER TABLE benchmark_metrics
    ADD COLUMN IF NOT EXISTS evaluation_level VARCHAR(32) NOT NULL DEFAULT 'recording';

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT con.conname
      INTO constraint_name
      FROM pg_constraint con
     WHERE con.conrelid = 'benchmark_metrics'::regclass
       AND con.contype = 'u'
       AND pg_get_constraintdef(con.oid) LIKE
           'UNIQUE (run_id, source_dataset, target_dataset, model_id, metric_name)%'
     LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE benchmark_metrics DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'benchmark_metrics'::regclass
           AND conname = 'benchmark_metrics_unique_level'
    ) THEN
        ALTER TABLE benchmark_metrics
            ADD CONSTRAINT benchmark_metrics_unique_level UNIQUE (
                run_id, source_dataset, target_dataset, model_id, evaluation_level, metric_name
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_benchmark_splits_run ON benchmark_splits(run_id);
CREATE INDEX IF NOT EXISTS idx_benchmark_metrics_run ON benchmark_metrics(run_id);
"""


@dataclass(frozen=True)
class CatalogRecording:
    dataset_name: str
    full_recording_id: str
    recording_name: str
    subject_id: str | None
    af_present: bool | None
    sampling_rate: int
    duration_seconds: float
    channel_names: list[str]
    parquet_path: str | None
    annotations_path: str | None

    def to_split_record(self) -> RecordingRecord:
        return RecordingRecord(
            dataset_name=self.dataset_name,
            full_recording_id=self.full_recording_id,
            subject_id=self.subject_id,
            af_present=self.af_present,
            duration_seconds=self.duration_seconds,
            metadata={
                "recording_name": self.recording_name,
                "sampling_rate": self.sampling_rate,
                "channel_names": self.channel_names,
                "parquet_path": self.parquet_path,
                "annotations_path": self.annotations_path,
            },
        )


class BenchmarkCatalog:
    """Postgres catalog access for benchmark metadata."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @classmethod
    def from_env(cls, database: DatabaseConfig | None = None) -> "BenchmarkCatalog":
        database = database or DatabaseConfig()
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError("psycopg2 is required for Postgres catalog access") from exc
        connection = psycopg2.connect(
            host=os.environ[database.host_env],
            port=int(os.environ[database.port_env]),
            dbname=os.environ[database.db_env],
            user=os.environ[database.user_env],
            password=os.environ[database.password_env],
        )
        return cls(connection)

    def close(self) -> None:
        self.connection.close()

    def ensure_schema(self) -> None:
        with self.connection.cursor() as cur:
            cur.execute(BENCHMARK_SCHEMA_SQL)
        self.connection.commit()

    def dataset_names(self) -> list[str]:
        with self.connection.cursor() as cur:
            cur.execute("SELECT name FROM datasets ORDER BY name")
            return [str(row[0]) for row in cur.fetchall()]

    def recordings_for_datasets(self, dataset_names: list[str], *, sample_rate_hz: int | None = None) -> list[CatalogRecording]:
        if not dataset_names:
            return []
        query = """
            SELECT
                d.name,
                r.full_recording_id,
                r.recording_name,
                r.subject_id,
                r.af_present,
                COALESCE(rr.target_sampling_rate, r.sampling_rate) AS sampling_rate,
                r.duration_seconds,
                r.channel_names,
                COALESCE(rr.parquet_path, r.parquet_path) AS parquet_path,
                COALESCE(rr.annotations_path, r.annotations_path) AS annotations_path
            FROM recordings r
            JOIN datasets d ON d.dataset_id = r.dataset_id
            LEFT JOIN resampled_recordings rr
                ON rr.recording_id = r.recording_id
                AND (%s IS NOT NULL AND rr.target_sampling_rate = %s)
            WHERE d.name = ANY(%s)
              AND (%s IS NULL OR COALESCE(rr.target_sampling_rate, r.sampling_rate) = %s)
            ORDER BY d.name, r.full_recording_id
        """
        with self.connection.cursor() as cur:
            cur.execute(query, (sample_rate_hz, sample_rate_hz, dataset_names, sample_rate_hz, sample_rate_hz))
            rows = cur.fetchall()
        return [
            CatalogRecording(
                dataset_name=str(row[0]),
                full_recording_id=str(row[1]),
                recording_name=str(row[2]),
                subject_id=row[3],
                af_present=row[4],
                sampling_rate=int(row[5]),
                duration_seconds=float(row[6]),
                channel_names=list(row[7] or []),
                parquet_path=row[8],
                annotations_path=row[9],
            )
            for row in rows
        ]

    def upsert_run(
        self,
        *,
        run_id: str,
        experiment_name: str,
        random_seed: int,
        config_hash: str,
        artifact_root_uri: str,
        resolved_config_uri: str | None = None,
        split_hash: str | None = None,
    ) -> None:
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO benchmark_runs (
                    run_id, experiment_name, random_seed, config_hash,
                    split_hash, artifact_root_uri, resolved_config_uri
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE
                SET experiment_name = EXCLUDED.experiment_name,
                    random_seed = EXCLUDED.random_seed,
                    config_hash = EXCLUDED.config_hash,
                    split_hash = EXCLUDED.split_hash,
                    artifact_root_uri = EXCLUDED.artifact_root_uri,
                    resolved_config_uri = EXCLUDED.resolved_config_uri
                """,
                (
                    run_id,
                    experiment_name,
                    random_seed,
                    config_hash,
                    split_hash,
                    artifact_root_uri,
                    resolved_config_uri,
                ),
            )
        self.connection.commit()

    def insert_split_assignments(self, run_id: str, assignments: list[dict[str, Any]]) -> None:
        with self.connection.cursor() as cur:
            for item in assignments:
                cur.execute(
                    """
                    INSERT INTO benchmark_splits (
                        run_id, dataset_name, full_recording_id, split, group_id, af_present
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, full_recording_id) DO UPDATE
                    SET dataset_name = EXCLUDED.dataset_name,
                        split = EXCLUDED.split,
                        group_id = EXCLUDED.group_id,
                        af_present = EXCLUDED.af_present
                    """,
                    (
                        run_id,
                        item["dataset_name"],
                        item["full_recording_id"],
                        item["split"],
                        item["group_id"],
                        item["af_present"],
                    ),
                )
        self.connection.commit()

    def upsert_model(self, *, run_id: str, source_dataset: str, model_id: str, artifact_uri: str) -> None:
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO benchmark_models (run_id, source_dataset, model_id, artifact_uri)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id, source_dataset, model_id) DO UPDATE
                SET artifact_uri = EXCLUDED.artifact_uri,
                    trained_at = NOW()
                """,
                (run_id, source_dataset, model_id, artifact_uri),
            )
        self.connection.commit()

    def upsert_metrics(
        self,
        *,
        run_id: str,
        source_dataset: str,
        target_dataset: str,
        model_id: str,
        evaluation_level: str = "recording",
        metrics: dict[str, float | None],
        artifact_uri: str | None = None,
    ) -> None:
        with self.connection.cursor() as cur:
            for name, value in metrics.items():
                cur.execute(
                    """
                    INSERT INTO benchmark_metrics (
                        run_id, source_dataset, target_dataset, model_id,
                        evaluation_level, metric_name, metric_value, artifact_uri
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (
                        run_id, source_dataset, target_dataset, model_id, evaluation_level, metric_name
                    )
                    DO UPDATE SET metric_value = EXCLUDED.metric_value,
                                  artifact_uri = EXCLUDED.artifact_uri,
                                  created_at = NOW()
                    """,
                    (
                        run_id,
                        source_dataset,
                        target_dataset,
                        model_id,
                        evaluation_level,
                        name,
                        value,
                        artifact_uri,
                    ),
                )
        self.connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, experiment_name, random_seed, config_hash, split_hash,
                       artifact_root_uri, resolved_config_uri, created_at
                FROM benchmark_runs
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "experiment_name": row[1],
            "random_seed": row[2],
            "config_hash": row[3],
            "split_hash": row[4],
            "artifact_root_uri": row[5],
            "resolved_config_uri": row[6],
            "created_at": row[7],
        }
