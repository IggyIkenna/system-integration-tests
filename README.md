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
| execution-results-api             | L5   | API contract stability          |
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
