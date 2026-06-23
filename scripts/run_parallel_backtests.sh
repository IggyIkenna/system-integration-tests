#!/usr/bin/env bash
# Epic: infrastructure_master
# Lifecycle: permanent
# Delete-when: NA
# Portable parallel backtest runner for CI.
# Uses local fixture files instead of GCS — no live API calls, no cloud credentials required.
#
# Usage:
#   ./scripts/run_parallel_backtests.sh [domain] [max_parallel] [seed]
#
# Arguments:
#   domain        - cefi | tradfi | defi | sports (default: cefi)
#   max_parallel  - maximum parallel jobs (default: 4)
#   seed          - random seed for deterministic output (default: 42)
#
# Examples:
#   ./scripts/run_parallel_backtests.sh cefi 4 42
#   ./scripts/run_parallel_backtests.sh defi 2 42
#
# Portable criteria enforced:
#   - CLOUD_PROVIDER=local (no GCS, no PubSub)
#   - All external data from tests/fixtures/ (no live API calls)
#   - Deterministic: BACKTEST_SEED is passed to all runners
#   - Exit 0 only if ALL domain backtests pass their acceptance gates

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${REPO_ROOT}/.." && pwd)"

DOMAIN="${1:-cefi}"
MAX_PARALLEL="${2:-4}"
SEED="${3:-42}"

# Validate domain
case "${DOMAIN}" in
  cefi|tradfi|defi|sports) ;;
  *)
    echo "[run_parallel_backtests] ERROR: Unknown domain '${DOMAIN}'. Use: cefi, tradfi, defi, sports"
    exit 1
    ;;
esac

echo "========================================"
echo "Portable Parallel Backtest Runner (CI)"
echo "========================================"
echo "Domain:       ${DOMAIN}"
echo "Max Parallel: ${MAX_PARALLEL}"
echo "Seed:         ${SEED}"
echo "Workspace:    ${WORKSPACE_ROOT}"
echo "========================================"

# Enforce no live API calls
export CLOUD_PROVIDER=local
export SERVICE_MODE=batch
export ENVIRONMENT=development
export GCP_PROJECT_ID=local-dev
export BACKTEST_SEED="${SEED}"
export USE_SECRET_MANAGER=false
export USE_MOCK_DATA=true
export BACKTEST_RAISE_EXCEPTION=false

RESULTS_DIR="${REPO_ROOT}/backtest_results/${DOMAIN}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "${RESULTS_DIR}"
echo "[run_parallel_backtests] Results directory: ${RESULTS_DIR}"

# Domain-specific runner dispatch
run_cefi() {
  local strategy_service_dir="${WORKSPACE_ROOT}/strategy-service"
  if [[ ! -d "${strategy_service_dir}" ]]; then
    echo "[run_parallel_backtests] ERROR: strategy-service not found at ${strategy_service_dir}"
    return 1
  fi
  echo "[run_parallel_backtests] Running CeFi portable backtests..."
  cd "${strategy_service_dir}"
  BACKTEST_SEED="${SEED}" python scripts/run_backtest_api.py \
    --domain cefi \
    --fixtures tests/fixtures/ \
    --output "${RESULTS_DIR}/cefi_result.json" \
    2>&1 | tee "${RESULTS_DIR}/cefi.log"
}

run_tradfi() {
  local execution_service_dir="${WORKSPACE_ROOT}/execution-service"
  if [[ ! -d "${execution_service_dir}" ]]; then
    echo "[run_parallel_backtests] ERROR: execution-service not found at ${execution_service_dir}"
    return 1
  fi
  echo "[run_parallel_backtests] Running TradFi portable backtests..."
  cd "${execution_service_dir}"
  BACKTEST_SEED="${SEED}" python scripts/runners/run_tradfi_l1_l2_backtests.py \
    2>&1 | tee "${RESULTS_DIR}/tradfi.log"
}

run_defi() {
  local execution_service_dir="${WORKSPACE_ROOT}/execution-service"
  if [[ ! -d "${execution_service_dir}" ]]; then
    echo "[run_parallel_backtests] ERROR: execution-service not found at ${execution_service_dir}"
    return 1
  fi
  echo "[run_parallel_backtests] Running DeFi portable backtests..."
  cd "${execution_service_dir}"
  BACKTEST_SEED="${SEED}" python scripts/runners/run_defi_backtests.py \
    2>&1 | tee "${RESULTS_DIR}/defi.log"
}

run_sports() {
  local strategy_service_dir="${WORKSPACE_ROOT}/strategy-service"
  if [[ ! -d "${strategy_service_dir}" ]]; then
    echo "[run_parallel_backtests] ERROR: strategy-service not found at ${strategy_service_dir}"
    return 1
  fi
  if [[ ! -f "${strategy_service_dir}/scripts/run_sports_arb_backtest.py" ]]; then
    echo "[run_parallel_backtests] ERROR: run_sports_arb_backtest.py not found."
    echo "  Expected: strategy-service/scripts/run_sports_arb_backtest.py"
    echo "  Create this script to implement the sports arb backtest."
    return 1
  fi
  echo "[run_parallel_backtests] Running Sports Arb portable backtest..."
  cd "${strategy_service_dir}"
  BACKTEST_SEED="${SEED}" python scripts/run_sports_arb_backtest.py \
    --fixtures tests/fixtures/sports_odds/ \
    --output "${RESULTS_DIR}/sports_backtest_result.json" \
    2>&1 | tee "${RESULTS_DIR}/sports.log"
}

# Run the selected domain
START_TIME=$(date +%s)
EXIT_CODE=0

case "${DOMAIN}" in
  cefi)   run_cefi   || EXIT_CODE=$? ;;
  tradfi) run_tradfi || EXIT_CODE=$? ;;
  defi)   run_defi   || EXIT_CODE=$? ;;
  sports) run_sports || EXIT_CODE=$? ;;
esac

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo "========================================"
echo "Backtest Summary"
echo "========================================"
echo "Domain:   ${DOMAIN}"
echo "Seed:     ${SEED}"
echo "Duration: ${DURATION}s"
echo "Results:  ${RESULTS_DIR}"
if [[ ${EXIT_CODE} -eq 0 ]]; then
  echo "Status:   PASSED"
else
  echo "Status:   FAILED (exit ${EXIT_CODE})"
fi
echo "========================================"

exit "${EXIT_CODE}"
