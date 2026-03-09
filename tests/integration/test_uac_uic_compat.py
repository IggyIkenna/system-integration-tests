"""Library integration tests — UAC/UIC canonical import compatibility.

These tests verify the import chain from unified-api-contracts and
unified-internal-contracts is intact without making any HTTP calls.
"""


def test_canonical_imports() -> None:
    """UAC domain module is importable and contains CanonicalBetOrder."""
    from unified_api_contracts.unified_normalised_contracts import domain

    assert hasattr(domain, "CanonicalBetOrder")


def test_uic_canonical_exports_present() -> None:
    """UIC top-level exports include key ML and execution contracts."""
    import unified_internal_contracts as uic

    assert hasattr(uic, "PredictionSnapshot")
    assert hasattr(uic, "CascadeConfig")
    assert hasattr(uic, "CascadePredictionEvent")
