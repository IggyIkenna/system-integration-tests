#!/usr/bin/env bash
# Epic: infrastructure_master
# Lifecycle: permanent
# Delete-when: NA
#
# run_cross_repo_invariants.sh — Tier B full-workspace SIT invariant runner.
#
# Runs the cross-repo invariants that CANNOT run in per-repo CI. In per-repo CI,
# workspace-qg renders a partial dep_repos set, so sibling repos are absent and these
# invariants SKIP (the guards in UAC's tests return early / pytest.skip when a sibling
# repo dir is missing). This runner REQUIRES the full active workspace to be assembled
# as sibling directories and FAILS LOUDLY if a required sibling is missing OR if a
# guarded invariant test reports SKIPPED — a skip here means the workspace was not
# assembled correctly, which is the exact failure mode this layer exists to catch.
#
# SSOT: unified-trading-pm/plans/active/issues/full_cicd_sit_target_state_2026_05_24.md § Tier B
# Owner: system-integration-tests slot | cadence: nightly + on staging promotion
# Verifier: full-workspace-sit.yml | last_executed: see workflow run history
#
# Usage:
#   WORKSPACE_ROOT=/path/to/workspace bash scripts/run_cross_repo_invariants.sh
# Env:
#   WORKSPACE_ROOT   workspace dir containing all repo siblings (default: parent of this repo)
#   UAC_PYTHON       python with unified-api-contracts + pytest installed
#                    (default: <WORKSPACE_ROOT>/unified-api-contracts/.venv/bin/python, else python3)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIT_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(dirname "$SIT_DIR")}"
UAC="$WORKSPACE_ROOT/unified-api-contracts"

if [ -n "${UAC_PYTHON:-}" ]; then
    PY="$UAC_PYTHON"
elif [ -x "$UAC/.venv/bin/python" ]; then
    PY="$UAC/.venv/bin/python"
else
    PY="python3"
fi

echo "── Tier B full-workspace SIT — cross-repo invariants ──"
echo "WORKSPACE_ROOT=$WORKSPACE_ROOT"
echo "UAC python=$PY"

FAILED=()
SKIPPED=()
PASSED=()

# ── 0. Required siblings — the repos the invariants validate ──────────────────
# A missing sibling here is a workspace-assembly bug, not a skip condition. This
# runner exists precisely to run the invariants WITH siblings present.
REQUIRED_SIBLINGS=(
    unified-api-contracts
    market-tick-data-service
    features-service
    instruments-service
    strategy-service
    unified-trading-library
    greeks-service
    unified-trading-api
    deployment-api
    ml-service
    market-data-processing-service
    trading-agent-service
    batch-live-reconciliation-service
    deployment-service
    alerting-service
    client-reporting-api
    fund-administration-service
)
missing=()
for r in "${REQUIRED_SIBLINGS[@]}"; do
    [ -d "$WORKSPACE_ROOT/$r" ] || missing+=("$r")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "❌ FATAL: full-workspace SIT requires all siblings assembled; missing: ${missing[*]}"
    echo "   Assemble the full workspace (all active repos as siblings) before running."
    exit 2
fi
echo "✅ all required siblings present"

# ── 0b. Coverage SSOT drift guard (WS-L SIT-rehome, Option B+) ─────────────────
# REQUIRED_SIBLINGS above is the SSOT for "which repos this suite actually validates". The LDR→main
# fleet promoter (a different repo) can't read this array, so it reads the MIRROR
# `sit_cross_repo_validated_repos` from workspace-manifest.json — and only trusts SIT_VALIDATED for
# those repos. If the two drift, the promoter would either forge a guarantee (manifest ⊋ suite) or
# needlessly block (manifest ⊊ suite). Assert they match EXACTLY so drift fails LOUD here.
_MANIFEST="$WORKSPACE_ROOT/unified-trading-pm/workspace-manifest.json"
if [ -f "$_MANIFEST" ]; then
    _drift=$("$PY" -c "
import json, sys
suite = sorted(sys.argv[1:])
mani = sorted(json.load(open('$_MANIFEST')).get('sit_cross_repo_validated_repos', []))
print('OK' if suite == mani else f'DRIFT suite={suite} manifest={mani}')
" "${REQUIRED_SIBLINGS[@]}")
    if [ "$_drift" != "OK" ]; then
        echo "❌ FATAL: SIT coverage SSOT drift — REQUIRED_SIBLINGS != manifest.sit_cross_repo_validated_repos"
        echo "   $_drift"
        echo "   Keep run_cross_repo_invariants.sh REQUIRED_SIBLINGS and the manifest list in sync."
        exit 2
    fi
    echo "✅ SIT coverage SSOT in sync (suite REQUIRED_SIBLINGS == manifest.sit_cross_repo_validated_repos)"
else
    echo "⚠️  manifest not found at $_MANIFEST — skipping coverage-drift assertion"
fi

# ── invariant runner helper ───────────────────────────────────────────────────
# run_pytest_invariant <label> <nodeid...>
# Fails on non-zero pytest exit OR on any "skipped" in the summary (skip == sibling
# guard tripped == workspace not assembled for this invariant).
run_pytest_invariant() {
    local label="$1"; shift
    echo ""
    echo "── invariant: $label ──"
    local out rc
    out="$(cd "$UAC" && "$PY" -m pytest "$@" -p no:cacheprovider -q --no-header 2>&1)"
    rc=$?
    echo "$out" | tail -25
    if [ $rc -ne 0 ]; then
        FAILED+=("$label (pytest rc=$rc)")
    elif echo "$out" | grep -qE "[0-9]+ skipped"; then
        SKIPPED+=("$label (reported skipped — siblings not seen)")
    else
        PASSED+=("$label")
    fi
}

# run_shell_invariant <label> <cmd...>
run_shell_invariant() {
    local label="$1"; shift
    echo ""
    echo "── invariant: $label ──"
    if "$@"; then
        PASSED+=("$label")
    else
        FAILED+=("$label (exit $?)")
    fi
}

# ── 1. feature-DAG SSOT (every declared service has a workspace dir) ──────────
run_pytest_invariant "feature-DAG SSOT" \
    "tests/test_feature_dag_ssot.py::test_every_service_has_workspace_directory"

# ── 2. cassette ↔ prod-consumer linkage (pytest gate) ─────────────────────────
run_pytest_invariant "cassette↔consumer linkage (pytest)" \
    "tests/test_cassette_orphan_checker.py::TestIntegrationOrphanCheck::test_no_unallowlisted_orphans"

# ── 3. cassette ↔ prod-consumer linkage (STEP 5.86 shell checker) ─────────────
run_shell_invariant "cassette↔consumer linkage (STEP 5.86)" \
    "$PY" "$UAC/scripts/check_cassette_prod_consumer_linkage.py"

# ── 4. data_type canonicalization across sibling venue_data_types.yaml ────────
run_pytest_invariant "data_type canonicalization (no banned aliases / in UAC)" \
    "tests/test_data_type_canonicalization.py"

# ── 5. unified-trading-library public API surface (cross-repo breaking-change gate) ──
run_pytest_invariant "UTL public API surface (cross-repo invariant)" \
    "tests/test_utl_cross_repo_invariant.py"

# ── 6. greeks-service greeks/risk output contract (cross-repo breaking-change gate) ──
run_pytest_invariant "greeks-service output contract (cross-repo invariant)" \
    "tests/test_greeks_service_cross_repo_invariant.py"

# ── 7. unified-trading-api public API contract (route modules + prefixes) ─────
run_pytest_invariant "unified-trading-api public API contract (cross-repo invariant)" \
    "tests/test_unified_trading_api_cross_repo_invariant.py"

# ── 8. deployment-ui API-contract consumption vs deployment-api ───────────────
run_pytest_invariant "deployment-ui API contract consumption (cross-repo invariant)" \
    "tests/test_deployment_ui_cross_repo_invariant.py"

# ── 9. ml-service model/feature contract (cross-repo breaking-change gate) ───
run_pytest_invariant "ml-service model/feature contract (cross-repo invariant)" \
    "tests/test_ml_service_cross_repo_invariant.py"

# ── 10. MDPS output contract (candle schema vs MTDS input + features consumer) ──
run_pytest_invariant "MDPS output contract (cross-repo invariant)" \
    "tests/test_mdps_cross_repo_invariant.py"

# ── 11. trading-agent-service directive-pipeline contract (vs strategy + execution) ──
run_pytest_invariant "trading-agent-service directive-pipeline contract (cross-repo invariant)" \
    "tests/test_trading_agent_service_cross_repo_invariant.py"

# ── 12. batch-live-reconciliation-service reconciliation contract ─────────────
run_pytest_invariant "batch-live-reconciliation-service reconciliation contract (cross-repo invariant)" \
    "tests/test_blrs_cross_repo_invariant.py"

# ── 13. deployment-api deploy/launch + repo-ci API contract ──────────────────
run_pytest_invariant "deployment-api deploy/launch + repo-ci API contract (cross-repo invariant)" \
    "tests/test_deployment_api_cross_repo_invariant.py"

# ── 14. deployment-service Python package surface + HTTP API contract ─────────
run_pytest_invariant "deployment-service package surface + HTTP API contract (cross-repo invariant)" \
    "tests/test_deployment_service_cross_repo_invariant.py"

# ── 15. alerting-service alert/notification contract ─────────────────────────
run_pytest_invariant "alerting-service alert/notification contract (cross-repo invariant)" \
    "tests/test_alerting_service_cross_repo_invariant.py"

# ── 16. client-reporting-api reporting contract ───────────────────────────────
run_pytest_invariant "client-reporting-api reporting contract (cross-repo invariant)" \
    "tests/test_client_reporting_api_cross_repo_invariant.py"

# ── 17. fund-administration-service fund-admin contract ───────────────────────
run_pytest_invariant "fund-administration-service fund-admin contract (cross-repo invariant)" \
    "tests/test_fund_administration_service_cross_repo_invariant.py"

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════ Tier B SIT summary ════════════════"
for p in "${PASSED[@]:-}";  do [ -n "$p" ] && echo "  ✅ $p"; done
for s in "${SKIPPED[@]:-}"; do [ -n "$s" ] && echo "  ⚠️  SKIPPED $s"; done
for f in "${FAILED[@]:-}";  do [ -n "$f" ] && echo "  ❌ $f"; done
echo "─────────────────────────────────────────────────────"

if [ ${#FAILED[@]} -gt 0 ] || [ ${#SKIPPED[@]} -gt 0 ]; then
    echo "❌ full-workspace SIT RED (${#FAILED[@]} failed, ${#SKIPPED[@]} skipped)"
    echo "   Known open finding: MTDS DeFi data_type alias drift —"
    echo "   unified-trading-pm/plans/active/issues/mtds_defi_datatype_alias_drift_2026_05_24.md"
    exit 1
fi
echo "✅ full-workspace SIT GREEN — all cross-repo invariants pass"
exit 0
