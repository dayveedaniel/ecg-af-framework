-- Create Airflow DB/user for local docker usage.
-- This runs only on first initialization of the postgres volume.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'airflow') THEN
    CREATE ROLE airflow LOGIN PASSWORD 'airflow';
  END IF;
END $$;

SELECT 'CREATE DATABASE airflow OWNER airflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow') \gexec

GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;
