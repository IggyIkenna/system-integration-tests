"""SIT integration test: every public UAC core class defined in scoped source must be in __all__.

Scope: structural completeness for UAC core directories only —
  unified_api_contracts/schemas/
  unified_api_contracts/unified_normalised_contracts/
  unified_api_contracts/trading_schemas.py
  unified_api_contracts/canonical_mappings.py

Mirrors the logic in unified-api-contracts/scripts/check_uac_completeness.py but
runs against the installed package. Intentionally excludes unified_api_contracts_external/
(40+ venue-specific subdirs; classes there are not promoted to top-level __all__).

Does NOT check which services use each contract — see the contract-adoption-check GHA job
in smoke-test-gate.yml for that.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Scoped source directories and files (mirrors check_uac_completeness.py SCAN_*)
# ---------------------------------------------------------------------------
_SCAN_SUBDIRS: list[str] = ["schemas", "unified_normalised_contracts"]
_SCAN_FILES: list[str] = ["trading_schemas.py", "canonical_mappings.py"]

_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        ".venv-workspace",
        "venv",
        "build",
        "dist",
        "node_modules",
        ".git",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "tests",
    }
)

# ---------------------------------------------------------------------------
# Classes intentionally NOT in UAC top-level __all__ (mirrors EXEMPT_MISSING
# in check_uac_completeness.py — keep in sync if source exemptions change)
# ---------------------------------------------------------------------------
_UAC_EXEMPT: frozenset[str] = frozenset(
    [
        # internal-base: private base classes used by the schema hierarchy
        "_CanonicalBase",
        "_ExternalBase",
        "_NormalisedBase",
        # normalizer-impl: NormalizerBase and concrete normalizer classes
        "NormalizerBase",
        "BinanceFuturesNormalizer",
        "BinanceSpotNormalizer",
        "BybitFuturesNormalizer",
        "BybitSpotNormalizer",
        "CoinbaseNormalizer",
        "DeribitNormalizer",
        "HyperliquidNormalizer",
        "IBKRNormalizer",
        "OKXFuturesNormalizer",
        "OKXSpotNormalizer",
        "BetfairNormalizer",
        "PolymarketNormalizer",
        # narrow-util
        "SchemaVersion",
        # analytics-specialty: factor + alternative data; not yet cross-service
        "FactorType",
        "AlternativeDataType",
        "CorrelationRegime",
        "FactorExposure",
        "FactorAttributionRecord",
        "FactorAttributionModel",
        "CrossAssetCorrelationMatrix",
        "CorrelationRegimeChange",
        "SentimentScore",
        "SatelliteObservation",
        "OptionsFlowRecord",
        "DarkPoolPrintRecord",
        "AlternativeDataSignal",
        # cex-withdrawal: per-venue CEX withdraw request/response
        "BinanceWithdrawRequest",
        "BinanceWithdrawResponse",
        "OKXWithdrawRequest",
        "OKXWithdrawResponse",
        "BybitWithdrawRequest",
        "BybitWithdrawResponse",
        "UpbitWithdrawRequest",
        "UpbitWithdrawResponse",
        "CoinbaseWithdrawRequest",
        "CoinbaseWithdrawResponse",
        # latency-specialty: sub-millisecond + co-location metrics
        "LatencyComponent",
        "LatencyPercentile",
        "TickToTradeMetric",
        "OrderLatencyRecord",
        "CoLocationPerformanceMetric",
        "NetworkJitterMetric",
        "SubMillisecondLatencyRecord",
        "LatencyBenchmarkReport",
        # prediction-market-arb: Polymarket/Betfair cross-venue arb signals
        "CrossVenueLink",
        "BucketMarket",
        "ProbabilityBucket",
        "SportsbookLink",
        "NegRiskBucket",
        "NegRiskArbSignal",
        "CrossVenueArbLeg",
        "CrossVenueArbSignal",
        "PredictionMarketUniverse",
        # protocol-sdk-action: DeFi protocol action parameter types
        "AaveDepositParams",
        "AaveBorrowParams",
        "AaveRepayParams",
        "AaveFlashLoanParams",
        "MorphoSupplyParams",
        "MorphoBorrowParams",
        "MorphoRepayParams",
        "MorphoFlashLoanParams",
        "EulerDepositParams",
        "EulerBorrowParams",
        "EulerRepayParams",
        "FluidDepositParams",
        "FluidBorrowParams",
        "FluidRepayParams",
        "LidoSubmitParams",
        "LidoRequestWithdrawalsParams",
        "CurveDepositParams",
        "CurveWithdrawParams",
        "CurveSwapParams",
        # rate-limit: venue rate-limit spec; UMI adapter-internal
        "HttpRateLimitHeaders",
        "VenueRateLimitSpec",
        # eth-transfer: raw Ethereum tx + ERC20 calldata
        "EthSendRawTransactionRequest",
        "EthSendRawTransactionResponse",
        "EthTransactionRequest",
        "EthSendTransactionRequest",
        "Erc20TransferCalldata",
        "Erc20TransferFromCalldata",
        # ws-internal: server-side WS health ping sentinel
        "HealthPingResponse",
    ]
)


def _get_uac_package_root() -> Path:
    import unified_api_contracts

    return Path(unified_api_contracts.__file__).parent


def _get_uac_all_set() -> set[str]:
    pkg_root = _get_uac_package_root()
    src = (pkg_root / "__init__.py").read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not all_match:
        raise RuntimeError("Could not parse __all__ from unified_api_contracts/__init__.py")
    return set(re.findall(r'"(\w+)"', all_match.group(1)))


def _scan_file(py_file: Path, pkg_root: Path, defined: dict[str, str]) -> None:
    if py_file.name == "__init__.py":
        return
    if any(part in _EXCLUDE_DIRS for part in py_file.parts):
        return
    try:
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
    except SyntaxError:
        return
    rel = str(py_file.relative_to(pkg_root.parent))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_") and node.name not in defined:
            defined[node.name] = rel


def _collect_uac_source_classes(pkg_root: Path) -> dict[str, str]:
    """AST-scan only scoped source dirs (mirrors check_uac_completeness.py logic)."""
    defined: dict[str, str] = {}
    for subdir_name in _SCAN_SUBDIRS:
        subdir = pkg_root / subdir_name
        if subdir.exists():
            for py_file in subdir.rglob("*.py"):
                _scan_file(py_file, pkg_root, defined)
    for filename in _SCAN_FILES:
        py_file = pkg_root / filename
        if py_file.exists():
            _scan_file(py_file, pkg_root, defined)
    return defined


# Resolved once at collection time
_UAC_PKG_ROOT: Path = _get_uac_package_root()
_UAC_EXPORTED: set[str] = _get_uac_all_set()
_UAC_SOURCE_CLASSES: dict[str, str] = _collect_uac_source_classes(_UAC_PKG_ROOT)

# Classes in source, not exempt, that must be in __all__
_UAC_REQUIRED: list[str] = sorted(
    name for name in _UAC_SOURCE_CLASSES if name not in _UAC_EXEMPT and not name.startswith("_")
)


# ---------------------------------------------------------------------------
# 1. Every non-exempt public source class must appear in __all__
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _UAC_REQUIRED, ids=_UAC_REQUIRED)
def test_uac_source_class_in_all(cls: str) -> None:
    """Public UAC core class defined in source must be exported in __all__."""
    assert cls in _UAC_EXPORTED, (
        f"unified_api_contracts.{cls} is defined in "
        f"{_UAC_SOURCE_CLASSES[cls]} but is not in __all__. "
        "Add it to unified_api_contracts/__init__.py or mark it exempt."
    )


# ---------------------------------------------------------------------------
# 2. Completeness regression guards
# ---------------------------------------------------------------------------


def test_uac_completeness_all_size() -> None:
    """__all__ must have at least 150 entries — regression guard against accidental shrink."""
    assert len(_UAC_EXPORTED) >= 150, (
        f"UAC __all__ has only {len(_UAC_EXPORTED)} entries. "
        "Something may have been accidentally removed from unified_api_contracts/__init__.py"
    )


def test_uac_completeness_source_class_count() -> None:
    """Scoped source scan must find at least 100 public classes — sanity guard."""
    assert len(_UAC_SOURCE_CLASSES) >= 100, (
        f"UAC scoped source scan found only {len(_UAC_SOURCE_CLASSES)} public classes. "
        "Source scan may have failed or package layout changed."
    )


def test_uac_completeness_no_gaps() -> None:
    """No non-exempt public UAC core class should be missing from __all__ (summary check)."""
    missing = [name for name in _UAC_REQUIRED if name not in _UAC_EXPORTED]
    assert not missing, f"{len(missing)} non-exempt public UAC core class(es) are missing from __all__:\n" + "\n".join(
        f"  - {name}  [{_UAC_SOURCE_CLASSES[name]}]" for name in sorted(missing)
    )
