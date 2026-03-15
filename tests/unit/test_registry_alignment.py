"""Registry alignment tests -- verify cross-repo enum/set consistency."""

from __future__ import annotations


def test_uac_instrument_type_superset_of_uci() -> None:
    """UAC InstrumentType must be a superset of UCI InstrumentType."""
    from unified_api_contracts.reference import InstrumentType as UAC_IT
    from unified_config_interface import InstrumentType as UCI_IT

    uac_values = {m.value for m in UAC_IT}
    uci_values = {m.value for m in UCI_IT}
    missing = uci_values - uac_values
    assert not missing, f"UCI has InstrumentType values not in UAC: {missing}"


def test_uci_instrument_type_values_match_uac() -> None:
    """UCI InstrumentType values must match UAC InstrumentType values."""
    from unified_api_contracts.reference import InstrumentType as UAC_IT
    from unified_config_interface import InstrumentType as UCI_IT

    uac_values = {m.value for m in UAC_IT}
    uci_values = {m.value for m in UCI_IT}
    assert uci_values == uac_values, (
        f"UCI/UAC InstrumentType value mismatch: UCI-only={uci_values - uac_values}, UAC-only={uac_values - uci_values}"
    )
