# AGENTS.md

## Setup

```bash
uv sync --extra dev
source .venv/bin/activate
```

## Quality Gates

```bash
bash scripts/quality-gates.sh
```

## Type Checking

```bash
timeout 120 basedpyright tests/
```

## Key Entry Points

- `tests/e2e/` — end-to-end integration tests
- `tests/smoke/` — smoke tests run after deployment

## Notes

- This repo contains SIT (System Integration Tests) — not a running service
- Part of the 4-repo deployment cluster; SIT must pass before `staging` → `main` promotion
- Requires ALL upstream services to be running/accessible
- Required env vars: `GCP_PROJECT_ID` plus service URLs for all tested services
- Requires GCP credentials: `gcloud auth application-default login`
- The three-tier branch model: staging lock is released only after SIT passes here
