# system-integration-tests

Layer 3 integration tests for the unified trading system. Interacts with running services via HTTP endpoints and checks GCS/PubSub state — **zero Python imports from services**.

## Test Layers

| Layer | Marker | Duration | Description |
|-------|--------|----------|-------------|
| 3a | `@pytest.mark.smoke` | < 5 min | Happy path: health checks, one venue + date + instrument |
| 3b | `@pytest.mark.full_e2e` | 15–30 min | Corner cases, auth flows, multi-date, perf baseline |

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

## SSOT

`unified-trading-codex/06-coding-standards/integration-testing-layers.md`
