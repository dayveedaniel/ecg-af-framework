#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_ENV_FILE="${DOCKER_ENV_FILE:-$ROOT_DIR/data-pipeline/.env.docker}"
HOST_ENV_FILE="${HOST_ENV_FILE:-$ROOT_DIR/data-pipeline/.env}"
DASHBOARD_HOST="${DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8765}"
RUNTIME_DIR="${RUNTIME_DIR:-$ROOT_DIR/.runtime}"
DASHBOARD_LOG="${DASHBOARD_LOG:-$RUNTIME_DIR/ecg-af-dashboard.log}"
DASHBOARD_PID_FILE="${DASHBOARD_PID_FILE:-$RUNTIME_DIR/ecg-af-dashboard.pid}"
JUPYTER_TOKEN="${JUPYTER_TOKEN:-ecg}"
KEEP_DASHBOARD="${KEEP_DASHBOARD:-0}"
FORCE_RECREATE="${FORCE_RECREATE:-0}"

cd "$ROOT_DIR"
mkdir -p "$RUNTIME_DIR"

if [[ ! -f "$DOCKER_ENV_FILE" ]]; then
  if [[ -f "$ROOT_DIR/data-pipeline/.env.docker.example" ]]; then
    cp "$ROOT_DIR/data-pipeline/.env.docker.example" "$DOCKER_ENV_FILE"
    echo "Created $DOCKER_ENV_FILE from example."
  else
    echo "Missing Docker env file: $DOCKER_ENV_FILE" >&2
    exit 2
  fi
fi

if [[ ! -f "$HOST_ENV_FILE" ]]; then
  if [[ -f "$ROOT_DIR/data-pipeline/.env.example" ]]; then
    cp "$ROOT_DIR/data-pipeline/.env.example" "$HOST_ENV_FILE"
    echo "Created $HOST_ENV_FILE from example."
  else
    echo "Missing host env file: $HOST_ENV_FILE" >&2
    exit 2
  fi
fi

if [[ "${SKIP_UV_SYNC:-0}" != "1" ]]; then
  uv sync --project py_ecg_benchmark_pkg
fi

COMPOSE_ARGS=(
  --env-file "$DOCKER_ENV_FILE"
  -f "$ROOT_DIR/data-pipeline/docker-compose.yaml"
  -f "$ROOT_DIR/experiments/data_pipeline_test/docker-compose.spark.yaml"
)
DOCKER_COMPOSE_CMD=(
  env
  -u S3_ENDPOINT_URL
  -u S3_ACCESS_KEY
  -u S3_SECRET_KEY
  -u POSTGRES_HOST
  -u POSTGRES_PORT
  -u POSTGRES_DB
  -u POSTGRES_USER
  -u POSTGRES_PASSWORD
  -u SPARK_MASTER
  docker
  compose
)

SERVICES=(postgres minio minio-init pgadmin spark-eda)
if [[ "${WITH_AIRFLOW:-0}" == "1" ]]; then
  COMPOSE_ARGS=(--profile airflow "${COMPOSE_ARGS[@]}")
  SERVICES+=(airflow-init airflow-webserver airflow-scheduler)
fi

UP_ARGS=(up -d)
if [[ "$FORCE_RECREATE" == "1" ]]; then
  UP_ARGS+=(--force-recreate)
fi
"${DOCKER_COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" "${UP_ARGS[@]}" "${SERVICES[@]}"

set -a
source "$HOST_ENV_FILE"
set +a
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1"
export no_proxy="${no_proxy:+$no_proxy,}localhost,127.0.0.1"

start_dashboard() {
  local detach_cmd=()
  if command -v setsid >/dev/null 2>&1; then
    detach_cmd=(setsid)
  elif command -v python3 >/dev/null 2>&1; then
    detach_cmd=(python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])')
  else
    detach_cmd=(nohup)
  fi

  "${detach_cmd[@]}" uv run --project py_ecg_benchmark_pkg ecg-bench dashboard \
    --host "$DASHBOARD_HOST" \
    --port "$DASHBOARD_PORT" \
    --log-dir experiments/data_pipeline_test \
    --runner-backend docker-compose \
    --runner-job-dir experiments/data_pipeline_test/dashboard_jobs \
    --docker-compose-file data-pipeline/docker-compose.yaml \
    --docker-compose-file experiments/data_pipeline_test/docker-compose.spark.yaml \
    --docker-env-file "$DOCKER_ENV_FILE" \
    >"$DASHBOARD_LOG" 2>&1 < /dev/null &
  echo "$!" > "$DASHBOARD_PID_FILE"
  echo "Started dashboard with PID $(cat "$DASHBOARD_PID_FILE")."
}

terminate_dashboard_pid() {
  local dashboard_pid="$1"
  local dashboard_pgid
  dashboard_pgid="$(ps -o pgid= -p "$dashboard_pid" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$dashboard_pgid" == "$dashboard_pid" ]]; then
    kill -TERM -- "-$dashboard_pid" 2>/dev/null || true
  else
    kill -TERM "$dashboard_pid" 2>/dev/null || true
  fi
}

stop_script_dashboard() {
  if [[ ! -f "$DASHBOARD_PID_FILE" ]]; then
    return
  fi
  local dashboard_pid
  dashboard_pid="$(cat "$DASHBOARD_PID_FILE")"
  if [[ -n "$dashboard_pid" ]] && kill -0 "$dashboard_pid" 2>/dev/null; then
    echo "Stopping dashboard PID $dashboard_pid so the latest code is loaded."
    terminate_dashboard_pid "$dashboard_pid"
    for _ in {1..30}; do
      if ! kill -0 "$dashboard_pid" 2>/dev/null; then
        break
      fi
      sleep 0.2
    done
  fi
  rm -f "$DASHBOARD_PID_FILE"
}

if [[ "$KEEP_DASHBOARD" == "1" ]] && [[ -f "$DASHBOARD_PID_FILE" ]] && kill -0 "$(cat "$DASHBOARD_PID_FILE")" 2>/dev/null; then
  echo "Dashboard already running with PID $(cat "$DASHBOARD_PID_FILE")."
else
  stop_script_dashboard
  start_dashboard
fi

cat <<EOF

ECG-AF stack is starting.

Dashboard:       http://$DASHBOARD_HOST:$DASHBOARD_PORT
Spark/Jupyter:   http://localhost:8889/lab?token=$JUPYTER_TOKEN
MinIO console:   http://localhost:9001
MinIO API:       http://localhost:9000
Postgres:        localhost:5432
pgAdmin:         http://localhost:5050
Artifact root:   s3://ecg-pipeline/benchmarks/runs/
Cache root:      s3://ecg-pipeline/benchmarks/cache/

Dashboard log:   $DASHBOARD_LOG
Dashboard PID:   $DASHBOARD_PID_FILE

Keep an already-running script-managed dashboard instead of restarting it:
  KEEP_DASHBOARD=1 ./scripts/start_ecg_af_stack.sh

Recreate containers so Docker-network env values from $DOCKER_ENV_FILE are applied:
  FORCE_RECREATE=1 ./scripts/start_ecg_af_stack.sh

Run a benchmark manually:
  uv run --project py_ecg_benchmark_pkg ecg-bench run --config py_ecg_benchmark_pkg/examples/showcase_smoke.yaml

Stop Docker services:
  ./scripts/stop_ecg_af_stack.sh
EOF
