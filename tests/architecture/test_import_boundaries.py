"""Import-boundary enforcement (P1.0d — workspace audit 2026-05-01).

Per ``unified_api_contracts.registry.service_contract_map.SERVICE_CONTRACT_MAP``,
each service declares ``forbidden_imports`` — module paths it must never import
because the boundary is enforced architecturally (e.g. strategy-service must
not reach into PBM internals; alerting must not compute domain formulas).

This test ast-walks every Python file in each service's source package and
fails if any forbidden import is found. Catches accidental cross-service
coupling before it lands.

Lives in system-integration-tests because it spans repos. Single test, one
canonical home — beats N per-repo copies that drift.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from unified_api_contracts.registry.service_contract_map import (
    SERVICE_CONTRACT_MAP,
    ServiceContract,
)

# Workspace root is two levels above this test file:
# system-integration-tests/tests/architecture/test_import_boundaries.py
_WORKSPACE_ROOT: Path = Path(__file__).resolve().parents[3]


# Map service-name → source package directory inside the repo.
# Both repo and package use snake_case derived from service-name with `-` → `_`.
def _package_dir(service_name: str) -> Path:
    repo = service_name
    pkg = service_name.replace("-", "_")
    return _WORKSPACE_ROOT / repo / pkg


def _read_ast(py_path: Path) -> ast.Module | None:
    """Parse a Python file to AST, returning None on read or syntax error."""
    try:
        source = py_path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return ast.parse(source, filename=str(py_path))
    except SyntaxError:
        return None


def _imports_from_node(node: ast.AST) -> list[str]:
    """Extract import paths from a single AST node."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        # `from a.b import x` -> "a.b"; level>0 (relative imports) stay inside
        # the package and are not boundary violations.
        return [node.module]
    return []


def _collect_imports(py_path: Path) -> list[str]:
    """Return a flat list of fully-qualified import paths used by ``py_path``."""
    tree = _read_ast(py_path)
    if tree is None:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        imports.extend(_imports_from_node(node))
    return imports


def _python_files(pkg_dir: Path) -> list[Path]:
    """All .py files under the source package (skip caches/builds)."""
    if not pkg_dir.is_dir():
        return []
    skip_parts = {"__pycache__", ".pytest_cache", "build", "dist"}
    out: list[Path] = []
    for path in pkg_dir.rglob("*.py"):
        if any(part in skip_parts for part in path.parts):
            continue
        out.append(path)
    return out


def _violations(contract: ServiceContract) -> list[tuple[Path, str]]:
    """Return every (file, forbidden_module) pair found in the service source."""
    pkg_dir = _package_dir(contract.service_name)
    found: list[tuple[Path, str]] = []
    for py in _python_files(pkg_dir):
        for imp in _collect_imports(py):
            # forbidden_exceptions take precedence — explicit allowlist beats
            # the prefix forbidden list. Each exception entry should carry a
            # comment in service_contract_map explaining why it's allowed.
            if imp in contract.forbidden_exceptions:
                continue
            for forbidden in contract.forbidden_imports:
                if imp == forbidden or imp.startswith(forbidden + "."):
                    found.append((py.relative_to(_WORKSPACE_ROOT), imp))
                    break
    return found


@pytest.mark.parametrize("service_name", sorted(SERVICE_CONTRACT_MAP.keys()))
def test_no_forbidden_imports(service_name: str) -> None:
    """Each service's source must not import any module on its forbidden list."""
    contract = SERVICE_CONTRACT_MAP[service_name]
    pkg_dir = _package_dir(service_name)
    if not pkg_dir.is_dir():
        pytest.skip(f"package dir {pkg_dir} not found in this checkout")
    if not contract.forbidden_imports:
        pytest.skip(f"{service_name} has no forbidden_imports declared")
    found = _violations(contract)
    if found:
        details = "\n".join(f"  {path}: imports {forbidden}" for path, forbidden in found)
        raise AssertionError(
            f"Forbidden cross-service imports in {service_name}:\n{details}\n\n"
            f"forbidden_imports declared in service_contract_map: "
            f"{sorted(contract.forbidden_imports)}"
        )


def test_every_emitted_event_has_canonical_producer() -> None:
    """Topic registry sanity: each emit/consume in service_contract_map must
    align with the EVENT_TOPIC_REGISTRY single-canonical-producer rule."""
    from unified_api_contracts.internal.event_topics import EVENT_TOPIC_REGISTRY

    emit_to_producers: dict[str, list[str]] = {}
    for contract in SERVICE_CONTRACT_MAP.values():
        for event in contract.emits:
            emit_to_producers.setdefault(event, []).append(contract.service_name)
    multi_producer = {e: producers for e, producers in emit_to_producers.items() if len(producers) > 1}
    if multi_producer:
        raise AssertionError(
            "Events with multiple canonical producers (each event must have ONE owner):\n"
            + "\n".join(f"  {e}: {producers}" for e, producers in multi_producer.items())
        )
    # Every event in the topic registry must have exactly one producer in the
    # contract map (or be reserved).
    for event_type, spec in EVENT_TOPIC_REGISTRY.items():
        if spec.reserved:
            continue
        registered_producers = emit_to_producers.get(event_type, [])
        if not registered_producers:
            # Cross-repo events whose producer lives outside the audited 10
            # (e.g. PriceSnapshot from market-tick-data-service, AlertDispatched
            # from alerting which legitimately produces to itself with empty
            # consumers). Those are declared in event_topics but not in
            # service_contract_map. Tolerate.
            assert spec.producer, f"{event_type} has no producer in topic registry"
            continue
        assert spec.producer in registered_producers, (
            f"{event_type}: topic registry declares producer={spec.producer!r} but "
            f"service_contract_map credits {registered_producers!r}"
        )
