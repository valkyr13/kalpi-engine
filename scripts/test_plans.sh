#!/usr/bin/env bash
#
# Smoke tests for POST /plans.
# For each case it shows the request, explains in plain words what should
# happen, calls the API, shows what actually came back, and marks PASS or FAIL.
#
# Usage:
#   ./scripts/test_plans.sh                                  # server on http://127.0.0.1:8000
#   BASE_URL=http://127.0.0.1:9000 ./scripts/test_plans.sh   # server somewhere else

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
PASSED=0
FAILED=0

# Pretty-print JSON; fall back to the raw text if it isn't valid JSON.
pretty() {
  python3 -m json.tool <<< "$1" 2>/dev/null || echo "$1"
}

run_test() {
  local name="$1" expected_status="$2" explanation="$3" payload="$4"

  echo "=================================================================="
  echo "TEST: $name"
  echo "=================================================================="
  echo "REQUEST (POST /plans):"
  pretty "$payload"
  echo
  echo "WHAT SHOULD HAPPEN (expected HTTP $expected_status):"
  echo "$explanation" | fold -s -w 70 | sed 's/^/  /'
  echo

  local response status body
  response=$(curl -s -w '\n%{http_code}' -X POST "$BASE_URL/plans" \
    -H 'Content-Type: application/json' -d "$payload")
  status=$(tail -n 1 <<< "$response")
  body=$(sed '$d' <<< "$response")

  echo "ACTUAL RESPONSE (HTTP $status):"
  pretty "$body"
  echo

  if [[ "$status" == "$expected_status" ]]; then
    echo ">> PASS"
    PASSED=$((PASSED + 1))
  else
    echo ">> FAIL: expected HTTP $expected_status, got HTTP $status"
    FAILED=$((FAILED + 1))
  fi
  echo
}

# Stop early with a clear message if the server isn't up.
if ! curl -s -o /dev/null "$BASE_URL/health"; then
  echo "Can't reach the server at $BASE_URL."
  echo "Start it first: 'fastapi dev app/main.py' or 'docker compose up --build'."
  exit 1
fi

run_test "First-time portfolio" 200 \
  "The user owns nothing yet, so every stock becomes a BUY order: buy 10 INFY and buy 40 ITC. Each order comes back with its own ID." \
  '{"mode":"FIRST_TIME","user_id":"u1","broker":"zerodha","stocks":[{"symbol":"INFY","quantity":10},{"symbol":"ITC","quantity":40}]}'

run_test "Rebalance with sell, buy and adjust" 200 \
  "Sells come first so their cash can pay for the buys: sell 40 ITC, then buy 3 TCS and buy 5 INFY. The INFY 'adjust' turns into a plain BUY order." \
  '{"mode":"REBALANCE","user_id":"u1","broker":"zerodha","sell":[{"symbol":"ITC","quantity":40}],"buy":[{"symbol":"TCS","quantity":3}],"adjust":[{"symbol":"INFY","side":"BUY","quantity":5}]}'

run_test "Empty rebalance" 422 \
  "There is nothing to sell, buy or adjust, so there is nothing to do. The request is rejected with a message saying at least one order is needed." \
  '{"mode":"REBALANCE","user_id":"u1","broker":"zerodha"}'

run_test "Same stock in sell and buy" 422 \
  "ITC can't be leaving and entering the portfolio at the same time, so the request is rejected and the error names ITC." \
  '{"mode":"REBALANCE","user_id":"u1","broker":"zerodha","sell":[{"symbol":"ITC","quantity":40}],"buy":[{"symbol":"ITC","quantity":40}]}'

run_test "Unknown mode" 422 \
  "The engine only understands FIRST_TIME and REBALANCE, so UPDATE is rejected and the error lists the two allowed modes." \
  '{"mode":"UPDATE","user_id":"u1","broker":"zerodha"}'

run_test "Negative quantity" 422 \
  "You can't buy -5 shares, so the request is rejected and the error points at the quantity field." \
  '{"mode":"FIRST_TIME","user_id":"u1","broker":"zerodha","stocks":[{"symbol":"INFY","quantity":-5}]}'

echo "=================================================================="
echo "SUMMARY: $PASSED passed, $FAILED failed"
echo "=================================================================="

# Exit non-zero if anything failed, so this can also run in CI later.
[[ $FAILED -eq 0 ]]