"""Mock scenario determinism and fast-forward smoke tests.

Validates that:
1. All 8 named scenarios load from YAML without error.
2. Same scenario name always produces identical synthetic data (seed determinism).
3. The delayed_data scenario with fast-forward_factor completes quickly.

These tests require UIC testing optional deps (numpy, pandas, pyarrow, pyyaml)
which are available in the SIT venv.
"""

from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.code_test


def _seed_spec() -> dict[object, object]:
    """Minimal spec used for determinism checks — no external files needed."""
    return {
        "gbm_params": {
            "BTC-USDT": {"vol": 0.5, "drift": 0.0, "base_price": 40000.0},
        },
        "defi_yield_params": {},
        "correlations": {},
    }


@pytest.mark.parametrize(
    "scenario_name",
    [
        "normal",
        "heavy",
        "light",
        "big_ranges",
        "bust",
        "no_system_overload",
        "missing_data",
        "delayed_data",
    ],
)
def test_all_scenarios_load(scenario_name: str) -> None:
    """Every named scenario YAML parses without error."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig

    scenario = MockScenario(scenario_name)
    cfg = ScenarioConfig.load(scenario)
    assert cfg.name == scenario
    assert cfg.seed > 0
    assert cfg.vol_multiplier > 0.0
    assert cfg.fast_forward_factor > 0.0


def test_scenario_deterministic() -> None:
    """Same scenario produces byte-identical output across two generator instances."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig
    from unified_internal_contracts.testing.synthetic import SyntheticDataGenerator

    cfg = ScenarioConfig.load(MockScenario.NORMAL)
    spec = _seed_spec()

    start = date(2025, 1, 1)
    end = date(2025, 1, 2)

    gen1 = SyntheticDataGenerator(spec, scenario=cfg)
    gen2 = SyntheticDataGenerator(spec, scenario=cfg)

    df1 = gen1.generate_ohlcv("BTC-USDT", "binance", start, end, "1h")
    df2 = gen2.generate_ohlcv("BTC-USDT", "binance", start, end, "1h")

    assert len(df1) > 0, "Generator produced empty dataframe"
    assert df1.equals(df2), "Same scenario + seed must produce identical output"


def test_scenario_different_seeds_differ() -> None:
    """Different scenarios (different seeds) produce different output."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig
    from unified_internal_contracts.testing.synthetic import SyntheticDataGenerator

    cfg_normal = ScenarioConfig.load(MockScenario.NORMAL)
    cfg_heavy = ScenarioConfig.load(MockScenario.HEAVY)
    spec = _seed_spec()

    start = date(2025, 1, 1)
    end = date(2025, 1, 2)

    df_normal = SyntheticDataGenerator(spec, scenario=cfg_normal).generate_ohlcv(
        "BTC-USDT", "binance", start, end, "1h"
    )
    df_heavy = SyntheticDataGenerator(spec, scenario=cfg_heavy).generate_ohlcv("BTC-USDT", "binance", start, end, "1h")

    assert not df_normal.equals(df_heavy), "Different scenarios must produce different output"


def test_delayed_scenario_fast_forwards() -> None:
    """delayed_data scenario config has correct fast-forward math.

    delayed_data: delay_ms=3600000 (1 hour), fast_forward_factor=3600.0.
    Effective sleep per tick = delay_ms / ff_factor / 1000 = 1.0s.
    4 ticks at 1s each = 4s total, under the 5s budget.
    """
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig

    cfg = ScenarioConfig.load(MockScenario.DELAYED_DATA)
    assert cfg.delay_ms == 3_600_000, "delayed_data must have 1-hour delay (3600000ms)"
    assert cfg.fast_forward_factor == 3600.0, "delayed_data must fast-forward 3600x"

    # Effective per-tick sleep (seconds) = delay_ms / ff_factor / 1000
    # = 3600000 / 3600 / 1000 = 1.0 second per tick
    effective_sleep_s = cfg.delay_ms / cfg.fast_forward_factor / 1000.0
    assert effective_sleep_s == pytest.approx(1.0), "Effective sleep should be ~1.0s per tick"

    # Wall-clock budget: 4 ticks × 1s = 4s < 5s
    ticks = 4
    expected_total_s = effective_sleep_s * ticks
    assert expected_total_s < 5.0, (
        f"{ticks} ticks at {effective_sleep_s:.3f}s each = {expected_total_s:.1f}s — must be < 5s"
    )
