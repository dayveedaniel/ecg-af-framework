#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_ENV_FILE="${DOCKER_ENV_FILE:-$ROOT_DIR/data-pipeline/.env.docker}"
DASHBOARD_PID_FILE="${DASHBOARD_PID_FILE:-$ROOT_DIR/.runtime/ecg-af-dashboard.pid}"
REMOVE_VOLUMES="${REMOVE_VOLUMES:-0}"
DASHBOARD_TIMEOUT_SECONDS="${DASHBOARD_TIMEOUT_SECONDS:-15}"

cd "$ROOT_DIR"

stop_dashboard() {
  if [[ ! -f "$DASHBOARD_PID_FILE" ]]; then
    echo "Dashboard PID file not found: $DASHBOARD_PID_FILE"
    return
  fi

  local dashboard_pid
  dashboard_pid="$(cat "$DASHBOARD_PID_FILE")"
  if [[ -z "$dashboard_pid" ]]; then
    rm -f "$DASHBOARD_PID_FILE"
    echo "Dashboard PID file was empty; removed it."
    return
  fi

  if ! kill -0 "$dashboard_pid" 2>/dev/null; then
    rm -f "$DASHBOARD_PID_FILE"
    echo "Dashboard PID $dashboard_pid is not running; removed stale PID file."
    return
  fi

  echo "Stopping dashboard process group $dashboard_pid..."
  local dashboard_pgid
  dashboard_pgid="$(ps -o pgid= -p "$dashboard_pid" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$dashboard_pgid" == "$dashboard_pid" ]]; then
    kill -TERM -- "-$dashboard_pid" 2>/dev/null || true
  else
    echo "PID $dashboard_pid is not a process-group leader; stopping only that process."
    kill -TERM "$dashboard_pid" 2>/dev/null || true
  fi

  local waited=0
  while kill -0 "$dashboard_pid" 2>/dev/null; do
    if (( waited >= DASHBOARD_TIMEOUT_SECONDS )); then
      echo "Dashboard did not stop within ${DASHBOARD_TIMEOUT_SECONDS}s; forcing it down."
      if [[ "$dashboard_pgid" == "$dashboard_pid" ]]; then
        kill -KILL -- "-$dashboard_pid" 2>/dev/null || true
      else
        kill -KILL "$dashboard_pid" 2>/dev/null || true
      fi
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  rm -f "$DASHBOARD_PID_FILE"
}

if [[ ! -f "$DOCKER_ENV_FILE" ]]; then
  echo "Docker env file not found: $DOCKER_ENV_FILE"
  echo "Skipping Docker Compose shutdown because the stack may not have been initialized from this checkout."
  stop_dashboard
  exit 0
fi

COMPOSE_ARGS=(
  --profile airflow
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
DOWN_ARGS=(down --remove-orphans)
if [[ "$REMOVE_VOLUMES" == "1" ]]; then
  DOWN_ARGS+=(--volumes)
fi

stop_dashboard

echo "Stopping Docker Compose services..."
"${DOCKER_COMPOSE_CMD[@]}" "${COMPOSE_ARGS[@]}" "${DOWN_ARGS[@]}"

cat <<EOF

ECG-AF stack stopped.

Preserved Docker volumes by default. To also delete Compose volumes:
  REMOVE_VOLUMES=1 ./scripts/stop_ecg_af_stack.sh
EOF
