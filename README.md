# system-integration-tests

Layer 3 integration tests for the unified trading system. Interacts with running services via HTTP endpoints and checks GCS/PubSub state — **zero Python imports from services**.

## Test Layers

| Layer | Marker                  | Duration  | Description                                              |
| ----- | ----------------------- | --------- | -------------------------------------------------------- |
| 3a    | `@pytest.mark.smoke`    | < 5 min   | Happy path: health checks, one venue + date + instrument |
| 3b    | `@pytest.mark.full_e2e` | 15–30 min | Corner cases, auth flows, multi-date, perf baseline      |

Layer 3a must pass before Layer 3b runs.

## Structure

```
tests/
  conftest.py          # shared fixtures: http client, GCS client, PubSub client
  smoke/               # Layer 3a
    test_pipeline_smoke.py
    test_api_smoke.py
  e2e/                 # Layer 3b
    test_pipeline_e2e.py
    test_auth_e2e.py
    test_aws_s3_smoke.py   # AWS S3 (skips without S3_TEST_BUCKET + boto3 creds)
```

## Operational modes (staging / E2E)

Declared combinations of `DATA_MODE`, `CLOUD_PROVIDER`, `TESTNET_MODE`, and related axes must match what services
actually deploy. SSOT for the matrix:
`unified-trading-codex/09-strategy/cross-cutting/operational-modes-matrix.md`. When CI or staging changes those env
defaults, extend smoke (3a) and full E2E (3b) assertions so testnet paths cannot silently hit production endpoints.

## Environment Variables

```bash
INSTRUMENTS_SERVICE_URL=http://localhost:8080
DEPLOYMENT_API_URL=http://localhost:8001
ERA_URL=http://localhost:8002
CRA_URL=http://localhost:8003
MDA_URL=http://localhost:8004
GCS_TEST_BUCKET=<sandbox-bucket>
GCP_PROJECT_ID=<project-id>
S3_TEST_BUCKET=<sandbox-bucket>   # optional, for AWS S3 integration tests
```

## Running Tests

```bash
# Install
uv pip install -e ".[dev]"

# Layer 3a smoke only (run first, <5 min)
pytest -m smoke -v

# Layer 3b full e2e (run after smoke passes, 15-30 min)
pytest -m full_e2e -v

# Both layers sequentially
pytest -m smoke -v && pytest -m full_e2e -v

# AWS S3 integration (skips without S3_TEST_BUCKET and boto3 credentials)
pytest -m integration -v tests/e2e/test_aws_s3_smoke.py
```

## AWS Test Path

AWS S3 smoke tests live in `tests/e2e/test_aws_s3_smoke.py`. They require:

- `S3_TEST_BUCKET` env var
- boto3 credentials (e.g. `~/.aws/credentials` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`)

Tests skip gracefully when credentials or bucket are not configured.

## Coverage Matrix Smoke

`tests/smoke/test_coverage_matrix_smoke.py` parametrises over representative
`(service × category × venue × data_type)` cells and asserts that TEST-bucket
state is internally consistent with the canonical per-category path layout
and manifest v5 schema.

Opt-in — the test skips by default:

```bash
export GCS_TEST_BUCKET_ENABLED=1
export GCP_PROJECT_ID=central-element-323112     # your project
# ADC / service-account creds must be present (gcloud auth application-default login)
pytest -m smoke -v tests/smoke/test_coverage_matrix_smoke.py
```

Pre-condition: TEST buckets must already contain parquet + manifest rows from
a prior run. Seed them by running the dev-local helper per service:

```bash
cd <service>
IS_TEST_RUN=true python scripts/smoke_matrix.py --execute --asset-group CEFI
```

The test enforces Steps 2 + 3 of the 3-step assertion contract (parquet
exists under the category-specific prefix; manifest row has
`capture_status in {captured, empty_confirmed}`). Step 1 (trigger) is
assumed — SIT's canon is HTTP + GCS/PubSub assertions, not subprocess CLI.

Pure-function helpers live in `tests/smoke/coverage_matrix_cells.py` and
are unit-tested in `tests/unit/test_coverage_matrix_cells.py` (19 tests).
Adding a new representative cell = append a row to `_CELLS`.

SSOT references:

- Per-category bucket + path layouts: `unified-trading-pm/codex/02-data/per-category-bucket-layouts.md`
- Manifest v5 schema: `unified-trading-pm/codex/02-data/availability-manifest-and-data-status.md`
- Playbook: `unified-trading-pm/codex/14-playbooks/smoke-testing-playbook.md`

## SIT Scope

Which repos are gated by SIT for staging → main promotion.

### Included in SIT

| Service                           | Tier | Reason                          |
| --------------------------------- | ---- | ------------------------------- |
| instruments-service               | L1   | Universe data gating downstream |
| market-data-processing-service    | L2   | Candle pipeline correctness     |
| features-delta-one-service        | L3   | Feature pipeline anchor         |
| features-volatility-service       | L3   | Vol surface features            |
| features-calendar-service         | L3   | Calendar/session features       |
| features-onchain-service          | L3   | Onchain features                |
| features-cross-instrument-service | L3   | Cross-instrument features       |
| features-multi-timeframe-service  | L3   | Multi-timeframe features        |
| features-sports-service           | L3   | Sports features                 |
| ml-inference-service              | L4   | Model inference pipeline        |
| strategy-service                  | L4   | Signal generation               |
| execution-service                 | L4   | Order execution                 |
| position-balance-monitor-service  | L4   | Position tracking               |
| risk-and-exposure-service         | L4   | Risk controls                   |
| pnl-attribution-service           | L4   | PnL correctness                 |
| alerting-service                  | L5   | Alert delivery                  |
| unified-trading-api               | L5   | API contract stability          |
| market-data-api                   | L5   | API contract stability          |
| client-reporting-api              | L5   | API contract stability          |
| deployment-api                    | L5   | Deployment control plane        |

### Excluded from SIT

| Repo                                                           | Reason                                             |
| -------------------------------------------------------------- | -------------------------------------------------- |
| market-tick-data-service                                       | Raw tick ingestion; separate feed validation suite |
| ml-training-service                                            | Async training jobs; separate training smoke tests |
| All T6 UI repos (`*-ui`)                                       | Playwright E2E in their own CI pipelines           |
| All library repos (`unified-*-library`, `unified-*-interface`) | Unit-tested in their own QG; no runtime SIT needed |

### Staging Gate Behaviour

`staging-to-main.yml` waits for **all included repos** to have passed SIT (`@pytest.mark.smoke` + `@pytest.mark.full_e2e`) before merging staging → main. Excluded repos are not awaited.

## SSOT

`unified-trading-codex/06-coding-standards/integration-testing-layers.md`
