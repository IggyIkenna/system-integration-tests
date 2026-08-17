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
# Per-invariant isolation: each invariant maps to a set of repos (or "all" for shared
# cross-repo invariants). A per-repo invariant failure marks only the affected repo(s)
# FAIL; other repos are unaffected. Results are written to /tmp/sit_per_repo_results.json
# so the stamp step can stamp SIT_VALIDATED only for repos that passed. The job still
# exits non-zero on any failure (the overall SIT result stays RED), but the per-repo
# JSON lets the stamp step be selective.
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

# Per-repo pass/fail map. Values: "PASS" | "FAIL". Populated after sibling check.
declare -A REPO_PASS

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
    agent-orchestrator
    unified-trading-system-ui
    deployment-ui
    execution-service
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

# Initialise all repos to PASS; invariant failures mark specific repos FAIL below.
for r in "${REQUIRED_SIBLINGS[@]}"; do
    REPO_PASS["$r"]="PASS"
done

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

# ── per-repo failure helper ───────────────────────────────────────────────────
# _mark_fail "all"|"<repo1>,<repo2>,..."  — marks repos FAIL in REPO_PASS.
# "all" marks every REQUIRED_SIBLING. Shared invariants (1-4) use "all"; per-repo
# invariants pass a CSV of the specific repos they exercise.
_mark_fail() {
    local repos_csv="$1"
    if [ "$repos_csv" = "all" ]; then
        for r in "${REQUIRED_SIBLINGS[@]}"; do REPO_PASS["$r"]="FAIL"; done
    else
        IFS=',' read -ra _rlist <<< "$repos_csv"
        for r in "${_rlist[@]}"; do
            REPO_PASS["$r"]="FAIL"
        done
    fi
}

# ── invariant runner helpers ──────────────────────────────────────────────────
# run_pytest_invariant <label> <repos_csv> <nodeid...>
#   repos_csv: "all" or comma-separated repo names this invariant validates.
#   Fails on non-zero pytest exit OR on any "skipped" in the summary (skip == sibling
#   guard tripped == workspace not assembled for this invariant).
run_pytest_invariant() {
    local label="$1"; local repos_csv="$2"; shift 2
    echo ""
    echo "── invariant: $label ──"
    local out rc
    out="$(cd "$UAC" && "$PY" -m pytest "$@" -p no:cacheprovider -q --no-header 2>&1)"
    rc=$?
    echo "$out" | tail -25
    if [ $rc -ne 0 ]; then
        FAILED+=("$label (pytest rc=$rc)")
        _mark_fail "$repos_csv"
    elif echo "$out" | grep -qE "[0-9]+ skipped"; then
        SKIPPED+=("$label (reported skipped — siblings not seen)")
        _mark_fail "$repos_csv"
    else
        PASSED+=("$label")
    fi
}

# run_shell_invariant <label> <repos_csv> <cmd...>
run_shell_invariant() {
    local label="$1"; local repos_csv="$2"; shift 2
    echo ""
    echo "── invariant: $label ──"
    if "$@"; then
        PASSED+=("$label")
    else
        FAILED+=("$label (exit $?)")
        _mark_fail "$repos_csv"
    fi
}

# ── 1. feature-DAG SSOT (every declared service has a workspace dir) ──────────
run_pytest_invariant "feature-DAG SSOT" "all" \
    "tests/test_feature_dag_ssot.py::test_every_service_has_workspace_directory"

# ── 2. cassette ↔ prod-consumer linkage (pytest gate) ─────────────────────────
run_pytest_invariant "cassette↔consumer linkage (pytest)" "all" \
    "tests/test_cassette_orphan_checker.py::TestIntegrationOrphanCheck::test_no_unallowlisted_orphans"

# ── 3. cassette ↔ prod-consumer linkage (STEP 5.86 shell checker) ─────────────
run_shell_invariant "cassette↔consumer linkage (STEP 5.86)" "all" \
    "$PY" "$UAC/scripts/check_cassette_prod_consumer_linkage.py"

# ── 4. data_type canonicalization across sibling venue_data_types.yaml ────────
run_pytest_invariant "data_type canonicalization (no banned aliases / in UAC)" "all" \
    "tests/test_data_type_canonicalization.py"

# ── 5. unified-trading-library public API surface (cross-repo breaking-change gate) ──
run_pytest_invariant "UTL public API surface (cross-repo invariant)" "unified-trading-library" \
    "tests/test_utl_cross_repo_invariant.py"

# ── 6. greeks-service greeks/risk output contract (cross-repo breaking-change gate) ──
run_pytest_invariant "greeks-service output contract (cross-repo invariant)" "greeks-service" \
    "tests/test_greeks_service_cross_repo_invariant.py"

# ── 7. unified-trading-api public API contract (route modules + prefixes) ─────
run_pytest_invariant "unified-trading-api public API contract (cross-repo invariant)" "unified-trading-api" \
    "tests/test_unified_trading_api_cross_repo_invariant.py"

# ── 8. deployment-ui API-contract consumption vs deployment-api ───────────────
run_pytest_invariant "deployment-ui API contract consumption (cross-repo invariant)" "deployment-ui,deployment-api" \
    "tests/test_deployment_ui_cross_repo_invariant.py"

# ── 9. ml-service model/feature contract (cross-repo breaking-change gate) ───
run_pytest_invariant "ml-service model/feature contract (cross-repo invariant)" "ml-service" \
    "tests/test_ml_service_cross_repo_invariant.py"

# ── 10. MDPS output contract (candle schema vs MTDS input + features consumer) ──
run_pytest_invariant "MDPS output contract (cross-repo invariant)" "market-data-processing-service" \
    "tests/test_mdps_cross_repo_invariant.py"

# ── 11. trading-agent-service directive-pipeline contract (vs strategy + execution) ──
run_pytest_invariant "trading-agent-service directive-pipeline contract (cross-repo invariant)" "trading-agent-service" \
    "tests/test_trading_agent_service_cross_repo_invariant.py"

# ── 12. batch-live-reconciliation-service reconciliation contract ─────────────
run_pytest_invariant "batch-live-reconciliation-service reconciliation contract (cross-repo invariant)" "batch-live-reconciliation-service" \
    "tests/test_blrs_cross_repo_invariant.py"

# ── 13. deployment-api deploy/launch + repo-ci API contract ──────────────────
run_pytest_invariant "deployment-api deploy/launch + repo-ci API contract (cross-repo invariant)" "deployment-api" \
    "tests/test_deployment_api_cross_repo_invariant.py"

# ── 14. deployment-service Python package surface + HTTP API contract ─────────
run_pytest_invariant "deployment-service package surface + HTTP API contract (cross-repo invariant)" "deployment-service" \
    "tests/test_deployment_service_cross_repo_invariant.py"

# ── 15. alerting-service alert/notification contract ─────────────────────────
run_pytest_invariant "alerting-service alert/notification contract (cross-repo invariant)" "alerting-service" \
    "tests/test_alerting_service_cross_repo_invariant.py"

# ── 16. client-reporting-api reporting contract ───────────────────────────────
run_pytest_invariant "client-reporting-api reporting contract (cross-repo invariant)" "client-reporting-api" \
    "tests/test_client_reporting_api_cross_repo_invariant.py"

# ── 17. fund-administration-service fund-admin contract ───────────────────────
run_pytest_invariant "fund-administration-service fund-admin contract (cross-repo invariant)" "fund-administration-service" \
    "tests/test_fund_administration_service_cross_repo_invariant.py"

# ── 18. agent-orchestrator role-registry / dispatch contract ──────────────────
run_pytest_invariant "agent-orchestrator role-registry / dispatch contract (cross-repo invariant)" "agent-orchestrator" \
    "tests/test_agent_orchestrator_cross_repo_invariant.py"

# ── 19. unified-trading-system-ui API-contract consumption ────────────────────
run_pytest_invariant "unified-trading-system-ui API-contract consumption (cross-repo invariant)" "unified-trading-system-ui" \
    "tests/test_unified_trading_system_ui_cross_repo_invariant.py"

# ── 20. execution-service interface/contract (orders, fills, instruction pipeline) ──
run_pytest_invariant "execution-service interface/contract (cross-repo invariant)" "execution-service" \
    "tests/test_execution_service_cross_repo_invariant.py"

# ── 21. deployment-ui TypeScript source self-check (Layer 2 — UI source routes) ─
# The first 3 tests in test_deployment_ui_cross_repo_invariant.py (invariant #8)
# validate deployment-api's route surface; the Layer-2 tests added here validate
# that deployment-ui's TypeScript API clients still reference those same routes.
# Both layers run as a single pytest call so any skip == workspace-assembly failure.
run_pytest_invariant "deployment-ui TypeScript source routes (cross-repo invariant — Layer 2)" "deployment-ui" \
    "tests/test_deployment_ui_cross_repo_invariant.py::test_deployment_ui_deployment_api_client_routes_stable" \
    "tests/test_deployment_ui_cross_repo_invariant.py::test_deployment_ui_repo_readiness_client_route_stable" \
    "tests/test_deployment_ui_cross_repo_invariant.py::test_deployment_ui_client_api_present"

# ── 22. cefi registry vs instruments-service's real expected-universe (Layer 2 backstop) ──
# Closes breaking_change_differ_blind_to_registry_data_dicts_2026_07_09.md's Layer-2 gap: on
# the 2026-07-07/08 incident, full-workspace-sit ran GREEN because no test re-derived IS's
# expected-universe from the live UAC registry. Loads instruments-service's dev-only
# scripts/expected_universe.py by file path (not part of the installable package) and runs
# the real build_expected('cefi') + the VENUE_DATA_TYPE_CAPABILITIES / CEFI_VENUE_FOLD
# structural invariants against the assembled workspace's live registry.
run_pytest_invariant "cefi registry vs instruments-service expected-universe (cross-repo invariant — Layer 2)" "instruments-service" \
    "tests/test_cefi_registry_expected_universe_invariant.py"

# ── 23. venue-coverage cascade, invariant 1 — MTDS batch ⟹ live (ratchet baseline) ──
# Codex SSOT: unified-trading-pm/codex/06-coding-standards/integration-testing-layers.md
# § "The venue-coverage cascade — operator ruling 2026-08-14". Origin:
# plans/active/issues/venue_coverage_position_read_vs_execute_asymmetry_2026_08_14.md.
# Fails only on a NEW gap beyond tests/data/mtds_batch_live_coverage_baseline.json — the
# pre-existing 109-venue backlog does not block the fleet; a regression does.
run_pytest_invariant "venue-coverage cascade — MTDS batch⟹live (cross-repo invariant)" "market-tick-data-service" \
    "tests/test_mtds_venue_coverage_cascade_invariant.py"

# ── 24. venue-coverage cascade, invariant 3 — strategy ⟹ execution reachability (ratchet baseline) ──
# Same codex SSOT + origin issue as #23. Checks CONNECTOR INSTANTIATION outside
# defi_execution/protocols/+tests/, not just module existence — a module existing is not
# the same as anything in production calling it (measured 2026-08-14 session 3: Morpho has
# no dispatcher path at all; Uniswap's is hardcoded to None). Fails only on a NEW
# regression beyond tests/data/execution_service_venue_reachability_baseline.json.
run_pytest_invariant "venue-coverage cascade — strategy⟹execution reachability (cross-repo invariant)" "strategy-service,execution-service" \
    "tests/test_execution_service_venue_coverage_cascade_invariant.py"

# ── 25. venue-coverage cascade, invariant 4 — LST token address drift (UAC ⟺ execution-service) ──
# Same origin issue as #23/#24. Guards the temporary window (until Chunk A's migration lands)
# where unified-api-contracts' lst_token_addresses.py and execution-service's per-protocol
# address constants both hold a copy of the same on-chain token address — a wrong address
# silently mis-reconciles a real position, no error anywhere. Unlike #23/#24 there is no
# tolerated backlog: every entry was migrated as an exact copy, so ANY mismatch is a real
# edit to one side that wasn't mirrored to the other. Once Chunk A lands (connectors import
# the address from UAC instead of duplicating it) this invariant becomes structurally
# untrippable and should be deleted, not left green.
run_pytest_invariant "LST token address drift — UAC ⟺ execution-service (cross-repo invariant)" "unified-api-contracts,execution-service" \
    "tests/test_lst_token_address_drift_invariant.py"

# ── 26. venue-coverage cascade, invariant 2 — MTDS venue ⟹ strategy position reader (ratchet baseline) ──
# Same codex SSOT + origin issue as #23/#24. Checks every MTDS batch-capture venue's protocol
# against strategy-service's per-venue batch/live/paper position-reading capability axis
# (position_interface/capabilities.py::position_read_mode_availability(), built
# strategy-service@926be71046). Fails only on a NEW gap beyond
# tests/data/strategy_position_read_mode_baseline.json — the pre-existing 106-venue backlog
# does not block the fleet; a regression does.
run_pytest_invariant "venue-coverage cascade — MTDS⟹strategy position-read (cross-repo invariant)" "market-tick-data-service,strategy-service" \
    "tests/test_strategy_position_read_mode_cascade_invariant.py"

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════ Tier B SIT summary ════════════════"
for p in "${PASSED[@]:-}";  do [ -n "$p" ] && echo "  ✅ $p"; done
for s in "${SKIPPED[@]:-}"; do [ -n "$s" ] && echo "  ⚠️  SKIPPED $s"; done
for f in "${FAILED[@]:-}";  do [ -n "$f" ] && echo "  ❌ $f"; done
echo "─────────────────────────────────────────────────────"

# ── per-repo results JSON ─────────────────────────────────────────────────────
# Written regardless of pass/fail so the workflow stamp step can read it under
# `if: always()` and stamp only the repos that passed their per-repo invariants.
_RESULTS_JSON=/tmp/sit_per_repo_results.json
{
    printf '{\n'
    _first=1
    for r in "${REQUIRED_SIBLINGS[@]}"; do
        _status="${REPO_PASS[$r]:-PASS}"
        [ "$_first" -eq 0 ] && printf ',\n'
        printf '  "%s": "%s"' "$r" "$_status"
        _first=0
    done
    printf '\n}\n'
} > "$_RESULTS_JSON"
echo "Per-repo SIT results → $_RESULTS_JSON"

if [ ${#FAILED[@]} -gt 0 ] || [ ${#SKIPPED[@]} -gt 0 ]; then
    echo "❌ full-workspace SIT RED (${#FAILED[@]} failed, ${#SKIPPED[@]} skipped)"
    echo "   Known open finding: MTDS DeFi data_type alias drift —"
    echo "   unified-trading-pm/plans/active/issues/mtds_defi_datatype_alias_drift_2026_05_24.md"
    exit 1
fi
echo "✅ full-workspace SIT GREEN — all cross-repo invariants pass"
exit 0
