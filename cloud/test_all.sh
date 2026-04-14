#!/usr/bin/env bash
# test_all.sh — ProtocolShift end-to-end smoke test (bash / WSL / Linux / macOS)
#
# Tests all 5 CRUD operations on every service:
#   REST  → Postgres (:8001), MongoDB (:8002), Redis (:8003)
#   gRPC  → Postgres (:50051), MongoDB (:50052), Redis (:50053)
#
# Requirements:
#   - curl (almost always available)
#   - jq   (brew install jq  /  apt install jq)
#   - grpcurl — for gRPC tests  (https://github.com/fullstorydev/grpcurl/releases)
#     If not found, gRPC tests are skipped with a warning.
#
# Usage:
#   bash test_all.sh
#   bash test_all.sh --skip-grpc     # REST only

set -euo pipefail

SKIP_GRPC=false
for arg in "$@"; do
  [[ "$arg" == "--skip-grpc" ]] && SKIP_GRPC=true
done

# ─────────────────────────────────────────────
# Colours
# ─────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; RESET='\033[0m'; BOLD='\033[1m'

PASS_COUNT=0; FAIL_COUNT=0

pass() { echo -e "  ${GREEN}[PASS]${RESET} $1"; ((PASS_COUNT++)) || true; }
fail() { echo -e "  ${RED}[FAIL]${RESET} $1${2:+  →  $2}"; ((FAIL_COUNT++)) || true; }
header() { echo -e "\n${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; echo -e "${CYAN}${BOLD}  $1${RESET}"; echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }
section() { echo -e "\n  ${YELLOW}── $1${RESET}"; }

# ─────────────────────────────────────────────
# REST test runner
# ─────────────────────────────────────────────
test_rest() {
  local NAME="$1" BASE="$2"
  header "REST — $NAME  ($BASE)"

  # Health check
  section "GET /healthz"
  STATUS=$(curl -sf "$BASE/healthz" | jq -r '.status' 2>/dev/null || echo "")
  if [[ "$STATUS" == "ok" ]]; then
    pass "Health check"
  else
    fail "Health check" "Service not reachable at $BASE — skipping"; return
  fi

  # CREATE
  section "POST /records  (Create)"
  CREATED=$(curl -sf -X POST "$BASE/records" \
    -H "Content-Type: application/json" \
    -d '{"payload":"benchmark-smoke-test"}')
  ID=$(echo "$CREATED" | jq -r '.id' 2>/dev/null || echo "")
  PAYLOAD=$(echo "$CREATED" | jq -r '.payload' 2>/dev/null || echo "")
  if [[ -n "$ID" && "$PAYLOAD" == "benchmark-smoke-test" ]]; then
    pass "Create  →  id=$ID"
  else
    fail "Create" "Response: $CREATED"; return
  fi

  # READ
  section "GET /records/$ID  (Read)"
  READ=$(curl -sf "$BASE/records/$ID")
  READ_PAYLOAD=$(echo "$READ" | jq -r '.payload' 2>/dev/null || echo "")
  if [[ "$READ_PAYLOAD" == "benchmark-smoke-test" ]]; then
    pass "Read    →  id=$ID payload='$READ_PAYLOAD'"
  else
    fail "Read" "response: $READ"
  fi

  # LIST
  section "GET /records  (ReadAll)"
  LIST=$(curl -sf "$BASE/records?limit=10&offset=0")
  TOTAL=$(echo "$LIST" | jq -r '.total' 2>/dev/null || echo "0")
  COUNT=$(echo "$LIST" | jq '.records | length' 2>/dev/null || echo "0")
  if [[ "$TOTAL" -ge 1 && "$COUNT" -ge 1 ]]; then
    pass "ReadAll →  total=$TOTAL, returned=$COUNT"
  else
    fail "ReadAll" "total=$TOTAL returned=$COUNT"
  fi

  # UPDATE
  section "PUT /records/$ID  (Update)"
  UPDATED=$(curl -sf -X PUT "$BASE/records/$ID" \
    -H "Content-Type: application/json" \
    -d '{"payload":"updated-smoke-test"}')
  UPD_PAYLOAD=$(echo "$UPDATED" | jq -r '.payload' 2>/dev/null || echo "")
  if [[ "$UPD_PAYLOAD" == "updated-smoke-test" ]]; then
    pass "Update  →  payload='$UPD_PAYLOAD'"
  else
    fail "Update" "response: $UPDATED"
  fi

  # DELETE
  section "DELETE /records/$ID  (Delete)"
  DELETED=$(curl -sf -X DELETE "$BASE/records/$ID")
  DEL_SUCCESS=$(echo "$DELETED" | jq -r '.success' 2>/dev/null || echo "")
  if [[ "$DEL_SUCCESS" == "true" ]]; then
    pass "Delete  →  id=$ID removed"
  else
    fail "Delete" "response: $DELETED"
  fi

  # VERIFY GONE
  section "GET /records/$ID  (Verify 404 after delete)"
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/records/$ID")
  if [[ "$HTTP_CODE" == "404" ]]; then
    pass "404 confirmed — record correctly removed"
  else
    fail "404 after delete" "Got HTTP $HTTP_CODE"
  fi
}

# ─────────────────────────────────────────────
# gRPC test runner (requires grpcurl)
# ─────────────────────────────────────────────
test_grpc() {
  local NAME="$1" HOST="$2"
  header "gRPC — $NAME  ($HOST)"

  grpc_call() { grpcurl -plaintext -d "$1" "$HOST" "benchmark.BenchmarkService/$2" 2>&1; }

  # CREATE
  section "Create RPC"
  CREATED=$(grpc_call '{"payload":"benchmark-smoke-test"}' "Create")
  ID=$(echo "$CREATED" | jq -r '.id' 2>/dev/null || echo "")
  PAYLOAD=$(echo "$CREATED" | jq -r '.payload' 2>/dev/null || echo "")
  if [[ -n "$ID" && "$PAYLOAD" == "benchmark-smoke-test" ]]; then
    pass "Create  →  id=$ID"
  else
    fail "Create" "grpcurl: $CREATED"; return
  fi

  # READ
  section "Read RPC"
  READ=$(grpc_call "{\"id\": $ID}" "Read")
  READ_PAYLOAD=$(echo "$READ" | jq -r '.payload' 2>/dev/null || echo "")
  if [[ "$READ_PAYLOAD" == "benchmark-smoke-test" ]]; then
    pass "Read    →  id=$ID payload='$READ_PAYLOAD'"
  else
    fail "Read" "$READ"
  fi

  # READALL
  section "ReadAll RPC"
  LIST=$(grpc_call '{"limit": 10, "offset": 0}' "ReadAll")
  TOTAL=$(echo "$LIST" | jq -r '.total' 2>/dev/null || echo "0")
  if [[ "$TOTAL" -ge 1 ]]; then
    pass "ReadAll →  total=$TOTAL"
  else
    fail "ReadAll" "$LIST"
  fi

  # UPDATE
  section "Update RPC"
  UPDATED=$(grpc_call "{\"id\": $ID, \"payload\": \"updated-smoke-test\"}" "Update")
  UPD_PAYLOAD=$(echo "$UPDATED" | jq -r '.payload' 2>/dev/null || echo "")
  if [[ "$UPD_PAYLOAD" == "updated-smoke-test" ]]; then
    pass "Update  →  payload='$UPD_PAYLOAD'"
  else
    fail "Update" "$UPDATED"
  fi

  # DELETE
  section "Delete RPC"
  DELETED=$(grpc_call "{\"id\": $ID}" "Delete")
  DEL_SUCCESS=$(echo "$DELETED" | jq -r '.success' 2>/dev/null || echo "")
  if [[ "$DEL_SUCCESS" == "true" ]]; then
    pass "Delete  →  id=$ID removed"
  else
    fail "Delete" "$DELETED"
  fi

  # VERIFY GONE
  section "Read after Delete (expect NOT_FOUND)"
  GONE=$(grpc_call "{\"id\": $ID}" "Read")
  if echo "$GONE" | grep -q "NOT_FOUND"; then
    pass "NOT_FOUND confirmed — record correctly removed"
  else
    fail "NOT_FOUND after delete" "Got: $GONE"
  fi
}

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
echo -e "\n${MAGENTA}${BOLD}╔══════════════════════════════════════════════════╗"
echo -e   "║   ProtocolShift — Full Smoke Test                ║"
echo -e   "╚══════════════════════════════════════════════════╝${RESET}"
echo      "  Started: $(date '+%Y-%m-%d %H:%M:%S')"

# REST
test_rest "PostgreSQL" "http://localhost:8001"
test_rest "MongoDB"    "http://localhost:8002"
test_rest "Redis"      "http://localhost:8003"

# gRPC
if [[ "$SKIP_GRPC" == "true" ]]; then
  echo -e "\n  ${YELLOW}[INFO] gRPC tests skipped (--skip-grpc)${RESET}"
elif ! command -v grpcurl &>/dev/null; then
  echo -e "\n  ${YELLOW}[WARN] grpcurl not found — skipping gRPC tests."
  echo -e "         Install: https://github.com/fullstorydev/grpcurl/releases${RESET}"
else
  test_grpc "PostgreSQL" "localhost:50051"
  test_grpc "MongoDB"    "localhost:50052"
  test_grpc "Redis"      "localhost:50053"
fi

# Summary
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo -e "\n${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  echo -e "  ${GREEN}${BOLD}Results:  $PASS_COUNT / $TOTAL passed ✓${RESET}"
else
  echo -e "  ${YELLOW}${BOLD}Results:  $PASS_COUNT / $TOTAL passed${RESET}"
  echo -e "  ${RED}${BOLD}$FAIL_COUNT test(s) FAILED — check service logs${RESET}"
fi
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"

exit $FAIL_COUNT
