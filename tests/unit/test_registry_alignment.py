"""Registry alignment tests -- verify cross-repo enum/set consistency."""

from __future__ import annotations


def test_uac_instrument_type_superset_of_uci() -> None:
    """UAC InstrumentType must be a superset of UCI InstrumentType."""
    from unified_api_contracts.reference import InstrumentType as UAC_IT
    from unified_config_interface.instrument import InstrumentType as UCI_IT

    uac_values = {m.value for m in UAC_IT}
    uci_values = {m.value for m in UCI_IT}
    missing = uci_values - uac_values
    assert not missing, f"UCI has InstrumentType values not in UAC: {missing}"


def test_uci_instrument_type_is_uac_reexport() -> None:
    """UCI InstrumentType should be the exact same class as UAC InstrumentType."""
    from unified_api_contracts.reference import InstrumentType as UAC_IT
    from unified_config_interface.instrument import InstrumentType as UCI_IT

    assert UAC_IT is UCI_IT, "UCI InstrumentType should re-export UAC InstrumentType, not define its own"
