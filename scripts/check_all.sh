#!/usr/bin/env bash
#
# Runs every check we have, in one go:
#   1. Unit tests (pytest)
#   2. Executor scenarios against the fake broker
#   3. API checks against a temporary server
#   4. (optional, with --docker) the same API checks against the Docker container
#
# Usage (from the project root, with the venv active):
#   ./scripts/check_all.sh            # stages 1-3
#   ./scripts/check_all.sh --docker   # stages 1-4 (stop anything else using port 8000 first)

cd "$(dirname "$0")/.." || exit 1  # always run from the project root

PORT=8765
RESULTS=()
FAILED=0

stage() {
  local name="$1"
  shift
  echo
  echo "######################################################################"
  echo "## $name"
  echo "######################################################################"
  if "$@"; then
    RESULTS+=("PASS  $name")
  else
    RESULTS+=("FAIL  $name")
    FAILED=1
  fi
}

wait_for_health() {
  local url="$1" tries="$2"
  for _ in $(seq 1 "$tries"); do
    curl -s -o /dev/null "$url/health" && return 0
    sleep 0.5
  done
  return 1
}

api_checks() {
  fastapi run app/main.py --port "$PORT" > /tmp/kalpi-check-server.log 2>&1 &
  local server_pid=$!
  wait_for_health "http://127.0.0.1:$PORT" 30
  BASE_URL="http://127.0.0.1:$PORT" ./scripts/test_plans.sh
  local result=$?
  kill "$server_pid" 2>/dev/null
  wait "$server_pid" 2>/dev/null
  if [[ $result -ne 0 ]]; then
    echo "(server log: /tmp/kalpi-check-server.log)"
  fi
  return $result
}

docker_checks() {
  docker compose up --build -d || return 1
  wait_for_health "http://127.0.0.1:8000" 120
  ./scripts/test_plans.sh
  local result=$?
  docker compose down
  return $result
}

stage "Unit tests (pytest)" python -m pytest -v
stage "Executor scenarios (fake broker)" python scripts/try_executor.py
stage "API checks (temporary server on port $PORT)" api_checks
if [[ "$1" == "--docker" ]]; then
  stage "API checks inside Docker (port 8000)" docker_checks
fi

echo
echo "######################################################################"
echo "## SUMMARY"
echo "######################################################################"
for r in "${RESULTS[@]}"; do
  echo "$r"
done
exit $FAILED
