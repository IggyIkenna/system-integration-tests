#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-pm/codex/06-coding-standards/quality-gates-service-template.sh
#
# Instructions for a new service:
#   1. Copy this to scripts/quality-gates.sh in your repo (rollout-quality-gates-unified.py does this)
#   2. SERVICE_NAME, SOURCE_DIR, and MIN_COVERAGE are set automatically by rollout (floor=70)
#   3. Set RUN_INTEGRATION=true only if your repo has integration tests
#   4. Add LOCAL_DEPS entries if your service has local editable deps (e.g. unified-trading-library)
SERVICE_NAME="system-integration-tests"
SOURCE_DIR="tests"
MIN_COVERAGE=2
RUN_INTEGRATION=false
PYTEST_WORKERS=${PYTEST_WORKERS:-2}
LOCAL_DEPS=()
    UAC_CANONICAL_EXEMPT=true   # SIT needs deep imports for contract validation
# Test-harness repo: all imports live under tests/ which the manifest-alignment scanner
# EXCLUDES (PM 2026-06-10 parity change) — without this skip every declared service dep
# reads as "declared but never imported" and reddens QG (jammed the exec-svc 0.6.0 cascade).
MANIFEST_ALIGNMENT_SKIP=true
WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
# lxml 5.4.0 PYSEC-2026-87 (fix=6.1.0): constrained to <6.0 by workspace; cannot upgrade without breaking
# uv.lock resolution — ignore until lxml constraint is lifted workspace-wide.
# joblib PYSEC-2024-277 + pyjwt PYSEC-2025-183: no-fix-available / workspace-wide ignore (base-service.sh).
PIP_AUDIT_EXTRA_ARGS="--ignore-vuln PYSEC-2024-277 --ignore-vuln PYSEC-2025-183 --ignore-vuln PYSEC-2026-87"
# CODEX_MAX_VIOLATIONS pinned 2026-06-11 per plans/active/codex_violations_ratchet_to_five_2026_06_10.md (census-honest: 0 current violations; ratchet-down only).
CODEX_MAX_VIOLATIONS=0
# Not a deployable HTTP/CLI service — this is a Layer-3 e2e/smoke test harness (no api/main.py,
# no ServiceBootstrap entrypoint). Skip the service-lifecycle infra checks (STEP 5.61/5.62).
SKIP_SERVICE_LIFECYCLE_STEPS=true
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"

# Codex enforcement: lifecycle triple (STARTED / STOPPED / FAILED) via UTL — not duplicated in service code.
# See: unified-trading-pm/codex/03-observability/lifecycle-events.md § Lifecycle Event QG Enforcement
log_section "[5.X/6] UEI LIFECYCLE EVENT ENFORCEMENT (STARTED/STOPPED/FAILED)"
if rg -q 'fastapi_uei_lifespan\s*\(' --type py "$SOURCE_DIR" 2>/dev/null; then
    log_success "UEI lifecycle: fastapi_uei_lifespan (canonical HTTP wiring in UTL)"
elif rg -q 'ServiceBootstrap\s*\(' --type py "$SOURCE_DIR" 2>/dev/null; then
    log_success "UEI lifecycle: ServiceBootstrap (canonical CLI wiring in UTL)"
else
    for event in STARTED STOPPED FAILED; do
        # -U: allow multiline call sites (e.g. log_event(\n  "STARTED", ...))
        run_timeout 30 rg "log_event.*\"${event}\"" "${SOURCE_DIR}" --type py -U -q \
            || log_warn "Missing log_event('${event}') in ${SERVICE_NAME} — see codex 03-observability/lifecycle-events.md"
    done
fi
