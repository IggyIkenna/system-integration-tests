"""Overnight integration tests (workspace audit Wave F, 2026-05-01).

These tests parametrise over (archetype x asset_group) cells from the UAC
``ASSET_GROUP_ONTOLOGY`` registry and exercise the full service mesh:
strategy -> PBM -> risk -> pnl-attribution -> alerting. They are the
canonical surface for verifying the batch=live invariant -- same
scenario through both modes, identical state hash.
"""
