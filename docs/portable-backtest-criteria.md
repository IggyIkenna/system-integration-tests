# Portable Backtest Criteria

**SSOT:** This document is referenced by `e2e_smoke_and_portable_backtests.plan.md` (todo: portable-criteria).
**Scope:** CeFi, TradFi, DeFi, and Sports portable backtests.

---

## 1. No Live API Calls in CI

All external data calls (Tardis, Databento, CCXT, Hyperliquid, odds/line feeds, etc.) **must** be
replaced by one of:

- **VCR cassettes** — recorded HTTP interactions stored in `tests/fixtures/cassettes/`
- **Fixture files** — pre-recorded parquet/CSV data stored in `tests/fixtures/`
- **Mock adapters** — in-process stubs implementing the interface (no network I/O)

Detection: any `pytest` run in CI that triggers a real network call to an external host (other than
`localhost`) must fail with an explicit error. Use `pytest-recording` or `responses` to enforce this.

### Enforcement pattern

```python
# In conftest.py for backtest tests:
import responses

@pytest.fixture(autouse=True)
def block_live_api_calls():
    """Fail immediately if any test makes a live external API call."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.passthrough_prefixes = ("http://localhost", "http://127.0.0.1")
        yield rsps
```

---

## 2. Deterministic Output

- Same input data + same random seed → same backtest output (trades, signals, PnL)
- Seed must be passed explicitly: `--seed 42` or via `BACKTEST_SEED=42`
- No `datetime.now()` or `random.random()` calls inside the strategy engine without seeding
- Floating-point results are deterministic to within 1e-8 (use `numpy` with fixed dtype/seed)

### Seed enforcement

```bash
# All backtest runners must accept --seed
BACKTEST_SEED=42 python -m execution_service backtest --config ... --seed 42
```

---

## 3. Batch-Live Symmetry

The backtest code path **must mirror** the live trading code path. Specifically:

| Constraint         | Requirement                                                       |
| ------------------ | ----------------------------------------------------------------- |
| Strategy logic     | Same Python class, same method signatures in batch and live modes |
| Signal generation  | `strategy_service` signal engine used in both modes               |
| Order construction | Same order builder in batch and live (NautilusTrader engine)      |
| Feature inputs     | Same UIC `FeaturesRecord` schema in batch and live                |
| Event output       | Same UIC `SignalVectorRecord` schema in batch and live            |

**Prohibited:** separate "backtest-only" strategy implementations that diverge from live logic.

The `SERVICE_MODE=batch` flag in execution-service selects the NautilusTrader `BacktestNode`
(instead of `TradingNode` for live), but the strategy actor, risk checks, and order routing
are identical.

---

## 4. Acceptance Criteria

Each portable backtest must exit `0` and produce a result artifact with all required fields.

### CeFi backtests (CEFI domain)

| Metric            | Gate                |
| ----------------- | ------------------- |
| Sharpe ratio      | > 1.2               |
| Max drawdown      | < 15%               |
| Number of trades  | ≥ 30 in test period |
| Annualised return | > 0%                |
| Exit code         | 0                   |

### TradFi backtests

| Metric           | Gate                |
| ---------------- | ------------------- |
| Sharpe ratio     | > 1.0               |
| Max drawdown     | < 12%               |
| Number of trades | ≥ 20 in test period |
| Exit code        | 0                   |

### DeFi backtests

| Metric           | Gate                |
| ---------------- | ------------------- |
| Sharpe ratio     | > 0.8               |
| Max drawdown     | < 20%               |
| Number of trades | ≥ 10 in test period |
| Exit code        | 0                   |

### Sports arb backtests

| Metric                           | Gate                                                             |
| -------------------------------- | ---------------------------------------------------------------- |
| Number of opportunities detected | ≥ 1                                                              |
| Number of trades                 | ≥ 1                                                              |
| Win rate                         | > 50%                                                            |
| Max drawdown                     | < 15%                                                            |
| Exit code                        | 0                                                                |
| Artifact written                 | `artifacts/sports_backtest_result.json` with all required fields |

### Required artifact fields (all backtests)

```json
{
  "n_opportunities": <int>,
  "n_trades": <int>,
  "pnl": <float>,
  "win_rate": <float>,
  "max_drawdown": <float>,
  "sharpe_ratio": <float>,
  "start_date": "<YYYY-MM-DD>",
  "end_date": "<YYYY-MM-DD>",
  "domain": "<cefi|tradfi|defi|sports>",
  "seed": <int>
}
```

---

## 5. Performance Gate

| Constraint                      | Limit                                            |
| ------------------------------- | ------------------------------------------------ |
| Per-strategy backtest wall time | < 5 minutes (or mark `@pytest.mark.integration`) |
| Memory peak                     | < 8 GB                                           |
| Temporary files                 | Cleaned up on exit                               |

---

## 6. VCR Cassette Storage Layout

```
tests/fixtures/
  cassettes/
    cefi/
      <venue>_<instrument>_<date>.yaml
    tradfi/
      <source>_<instrument>_<date>.yaml
    defi/
      <protocol>_<pool>_<date>.yaml
  sports_odds/
    <bookmaker>_<match>_<date>.yaml
  market_data/
    instruments_<category>_<date>.parquet
    ohlcv_<instrument>_<date>.parquet
```

Recording: use `pytest-recording` with `--record-mode=once` for initial cassette creation.
Replay: CI always uses `--record-mode=none` (no network calls permitted).

---

## 7. Run Commands

### CeFi

```bash
cd strategy-service
./scripts/run_parallel_backtests.sh <grid_id> cefi 4
# Or portable CI version:
BACKTEST_SEED=42 python scripts/run_backtest_api.py --domain cefi --fixtures tests/fixtures/
```

### TradFi

```bash
cd execution-service
BACKTEST_SEED=42 python scripts/runners/run_tradfi_l1_l2_backtests.py
```

### DeFi

```bash
cd execution-service
BACKTEST_SEED=42 python scripts/runners/run_defi_backtests.py
```

### Sports Arb

```bash
cd strategy-service
BACKTEST_SEED=42 python scripts/run_sports_arb_backtest.py \
  --fixtures tests/fixtures/sports_odds/ \
  --output artifacts/sports_backtest_result.json
```

---

## References

- `e2e_smoke_and_portable_backtests.plan.md`
- `unified-trading-codex/integration-testing-layers.md`
- `batch-live-symmetry.mdc` (cursor rule)
- `VCR_CREDENTIAL_RECORDING_PLAN.md`
- `system-integration-tests/README.md`
