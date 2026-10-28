#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE="${1:-.env.docker}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  echo "Tip: cp .env.docker.example .env.docker" >&2
  exit 2
fi

docker compose --env-file "$ENV_FILE" --profile airflow -f docker-compose.yaml up -d \
  postgres minio minio-init pgadmin airflow-init airflow-webserver airflow-scheduler

echo "Up:"
echo "  Airflow: http://localhost:8080"
echo "  MinIO:   http://localhost:9001"
echo "  pgAdmin: http://localhost:5050"

