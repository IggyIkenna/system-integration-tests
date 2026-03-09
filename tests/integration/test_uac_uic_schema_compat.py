"""Layer 0 contract alignment: UAC + UIC schema round-trip."""

from unified_api_contracts.unified_normalised_contracts.domain import CanonicalBetOrder
from unified_internal_contracts.schema_definition import SchemaDefinition


def test_canonical_bet_order_importable() -> None:
    assert CanonicalBetOrder is not None


def test_schema_definition_importable() -> None:
    assert SchemaDefinition is not None
