-- ECG Pipeline metadata catalog schema (Module 3)

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id          SERIAL PRIMARY KEY,
    name                VARCHAR(255) UNIQUE NOT NULL,
    display_name        VARCHAR(512),
    description         TEXT,
    source_url          VARCHAR(1024),
    license             VARCHAR(255),
    citation            TEXT,
    geographic_origin   VARCHAR(255),
    num_subjects        INTEGER,
    num_recordings      INTEGER,
    has_beat_annotations        BOOLEAN DEFAULT FALSE,
    has_rhythm_annotations      BOOLEAN DEFAULT FALSE,
    has_delineation_annotations BOOLEAN DEFAULT FALSE,
    ingestion_timestamp TIMESTAMPTZ DEFAULT NOW(),
    pipeline_version    VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS recordings (
    recording_id        SERIAL PRIMARY KEY,
    dataset_id          INTEGER REFERENCES datasets(dataset_id),
    recording_name      VARCHAR(255) NOT NULL,
    full_recording_id   VARCHAR(512) UNIQUE NOT NULL,

    sampling_rate       INTEGER NOT NULL,
    original_sampling_rate INTEGER NOT NULL,
    num_channels        INTEGER NOT NULL,
    channel_names       TEXT[],
    duration_seconds    FLOAT NOT NULL,
    num_samples         BIGINT NOT NULL,
    source_format       VARCHAR(50),

    subject_id          VARCHAR(255),
    age                 INTEGER,
    sex                 VARCHAR(10),

    has_beat_annotations        BOOLEAN DEFAULT FALSE,
    has_rhythm_annotations      BOOLEAN DEFAULT FALSE,
    has_delineation_annotations BOOLEAN DEFAULT FALSE,
    num_beat_annotations        INTEGER DEFAULT 0,
    num_rhythm_episodes         INTEGER DEFAULT 0,
    af_present                  BOOLEAN DEFAULT NULL, -- TRUE=AF found, FALSE=annotated but no AF, NULL=unknown

    parquet_path                VARCHAR(1024),
    annotations_path            VARCHAR(1024),

    split                       VARCHAR(20),

    ingestion_timestamp TIMESTAMPTZ DEFAULT NOW(),
    pipeline_version    VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS resampled_recordings (
    id                  SERIAL PRIMARY KEY,
    recording_id        INTEGER REFERENCES recordings(recording_id),
    target_sampling_rate INTEGER NOT NULL,
    parquet_path        VARCHAR(1024) NOT NULL,
    annotations_path    VARCHAR(1024),
    processing_timestamp TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(recording_id, target_sampling_rate)
);

CREATE INDEX IF NOT EXISTS idx_recordings_dataset ON recordings(dataset_id);
CREATE INDEX IF NOT EXISTS idx_recordings_af ON recordings(af_present) WHERE af_present = TRUE;
CREATE INDEX IF NOT EXISTS idx_recordings_split ON recordings(split);
CREATE INDEX IF NOT EXISTS idx_recordings_sr ON recordings(sampling_rate);
CREATE INDEX IF NOT EXISTS idx_resampled_rate ON resampled_recordings(target_sampling_rate);
