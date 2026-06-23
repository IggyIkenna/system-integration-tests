# Epic: infrastructure_master
# Lifecycle: permanent
# Delete-when: NA
"""Compute the SIT repo-slice for a gate run.

Usage:
    python scripts/compute_sit_slice.py --changed <repo1> [<repo2> ...] [--manifest <path>] [--topology <path>]

Outputs to stdout in the format consumed by smoke-test-gate.yml::

    SLICE_REPOS_JSON=["execution-service","strategy-service"]
    SLICE_MODE=targeted        # or BROAD when UAC/UTL changed, or EXEMPT when only PM changed
    PYTEST_FILTER=-k "execution_service or strategy_service"

Exit codes:
    0 — slice computed (or BROAD/EXEMPT emitted); caller reads env-style key=value lines from stdout
    1 — fatal error (parse failure, missing manifest); safety fallback = BROAD

Algorithm
---------
1. Parse the ``--changed`` repo set (may be empty).
2. Filter to repos with ``status == "active"`` in the PM manifest.  System-integration-tests
   and unified-trading-pm are always excluded from the slice (SIT is exempt; PM bypasses SIT).
3. Universal-dep broadening: if ``unified-api-contracts`` OR ``unified-trading-library`` appear
   in the changed set → emit ``SLICE_MODE=BROAD`` (full SIT; return all active, non-exempt repos).
4. PM-only change: if the filtered changed set is empty after removing exempt repos → emit
   ``SLICE_MODE=EXEMPT`` with ``SLICE_REPOS_JSON=[]``.
5. Compute transitive dependents via reverse-BFS over:
   a. ``workspace-manifest.json repositories[r].dependencies[].name`` edges (primary)
   b. ``configs/runtime-topology.yaml service_flows[].producer/consumer`` edges (supplementary)
6. Scope = changed-set UNION transitive-dependents, filtered to active & non-exempt.
7. Build a pytest marker-union expression from the slice repos for -k filtering.

Safety fallback (HARD rule from design §299):
   If the changed set CANNOT be determined (empty argv, parse error, missing manifest) → BROAD.
   Never under-test.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path
from typing import cast

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# These repos — if changed — trigger the full suite (deps of ~everything)
UNIVERSAL_DEPS: frozenset[str] = frozenset(
    {
        "unified-api-contracts",
        "unified-trading-library",
    }
)

# These repos are always excluded from the SIT slice:
# - system-integration-tests: this repo itself (not a service)
# - unified-trading-pm: docs/devops bypass (PM→QG→staging/main, no SIT)
EXEMPT_REPOS: frozenset[str] = frozenset(
    {
        "system-integration-tests",
        "unified-trading-pm",
    }
)

# Where the PM manifest and runtime-topology live relative to the
# system-integration-tests repo root.  Adjusted at runtime by --manifest /
# --topology flags.
DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent.parent.parent / "unified-trading-pm" / "workspace-manifest.json"
DEFAULT_TOPOLOGY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "unified-trading-pm" / "configs" / "runtime-topology.yaml"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_manifest(path: Path) -> dict[str, object]:
    # json.loads returns Any; we validate shape here and cast.
    parsed = cast(object, json.loads(path.read_text()))
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object at {path}, got {type(parsed)}")
    return cast(dict[str, object], parsed)


def _load_topology(path: Path) -> dict[str, object]:
    """Load runtime-topology.yaml.  Returns {} if yaml is unavailable (not installed in this venv)."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        # yaml not in venv — topology edges degraded to empty; manifest-dep edges still used.
        print("WARN: PyYAML not available; topology edges omitted from dep graph", file=sys.stderr)
        return {}
    try:
        # yaml.safe_load returns Any; validate + cast.
        result = cast(object, yaml.safe_load(path.read_text()))
        if not isinstance(result, dict):
            return {}
        return cast(dict[str, object], result)
    except Exception as exc:
        print(f"WARN: failed to parse runtime-topology at {path}: {exc}", file=sys.stderr)
        return {}


def _get_obj(d: dict[str, object], key: str) -> object:
    """Typed wrapper around dict.get that returns object (not Unknown)."""
    return d.get(key)


def _active_repos(manifest: dict[str, object]) -> set[str]:
    """Return the set of repos with status == 'active' in the manifest."""
    repos_raw = _get_obj(manifest, "repositories")
    if not isinstance(repos_raw, dict):
        return set()
    repos = cast(dict[str, object], repos_raw)
    result: set[str] = set()
    for name, meta in repos.items():
        if not isinstance(meta, dict):
            continue
        status = _get_obj(cast(dict[str, object], meta), "status")
        if status == "active":
            result.add(name)
    return result


def _build_reverse_dep_graph(
    manifest: dict[str, object],
    topology: dict[str, object],
    active: set[str],
) -> dict[str, set[str]]:
    """Build a reverse dependency graph: dep -> {repos that depend on dep}.

    Sources:
    1. manifest.repositories[r].dependencies[].name  (package-level, primary)
    2. runtime-topology.yaml service_flows[].producer/consumer  (runtime-edges, supplementary)
    """
    # reverse_graph[dep] = set of repos that depend on dep
    reverse_graph: dict[str, set[str]] = {r: set() for r in active}

    # --- Source 1: manifest package deps ---
    repos_raw = _get_obj(manifest, "repositories")
    if isinstance(repos_raw, dict):
        repos_meta = cast(dict[str, object], repos_raw)
        for repo_name, meta in repos_meta.items():
            if not isinstance(meta, dict):
                continue
            meta_d = cast(dict[str, object], meta)
            deps_raw = _get_obj(meta_d, "dependencies")
            if not isinstance(deps_raw, list):
                continue
            for dep in cast(list[object], deps_raw):
                if not isinstance(dep, dict):
                    continue
                dep_d = cast(dict[str, object], dep)
                dep_name_raw = _get_obj(dep_d, "name")
                if not isinstance(dep_name_raw, str):
                    continue
                dep_name: str = dep_name_raw
                if dep_name and repo_name in active:
                    reverse_graph.setdefault(dep_name, set()).add(repo_name)

    # --- Source 2: topology service_flows runtime edges ---
    flows_raw = _get_obj(topology, "service_flows")
    if isinstance(flows_raw, list):
        for flow in cast(list[object], flows_raw):
            if not isinstance(flow, dict):
                continue
            flow_d = cast(dict[str, object], flow)
            producer_raw = _get_obj(flow_d, "producer")
            consumer_raw = _get_obj(flow_d, "consumer")
            if not isinstance(producer_raw, str) or not isinstance(consumer_raw, str):
                continue
            producer: str = producer_raw
            consumer: str = consumer_raw
            if producer and consumer and consumer in active:
                # consumer depends on producer at runtime
                reverse_graph.setdefault(producer, set()).add(consumer)

    return reverse_graph


def _transitive_dependents(
    changed: set[str],
    reverse_graph: dict[str, set[str]],
) -> set[str]:
    """BFS over the reverse-dep graph to find all transitive dependents of 'changed'."""
    visited: set[str] = set()
    queue: deque[str] = deque(changed)
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for dependent in reverse_graph.get(node, set()):
            if dependent not in visited:
                queue.append(dependent)
    return visited


def _repo_to_marker(repo: str) -> str:
    """Convert a repo name to a pytest -k expression fragment.

    The marker fragment matches tests that import/touch the repo via the
    SIT naming convention.  We use a loose keyword match on the normalised
    repo slug (hyphens → underscores) so that, for example, a test file
    called ``test_market_tick_data_service.py`` or a ``pytestmark`` that
    includes the slug in its name is selected.

    Note: the full-suite ``BROAD`` path never calls this function; it runs
    ``pytest tests/`` without ``-k`` filtering.
    """
    return repo.replace("-", "_")


def _build_pytest_filter(repos: set[str]) -> str:
    """Build a pytest ``-k`` expression that selects tests touching any repo in the slice."""
    if not repos:
        return ""
    parts = sorted(_repo_to_marker(r) for r in repos)
    return " or ".join(parts)


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


def compute_slice(
    changed_repos: list[str],
    manifest_path: Path,
    topology_path: Path,
) -> tuple[str, list[str], str]:
    """Return (mode, slice_repos, pytest_filter).

    mode is one of:
    - "BROAD"   — full SIT (universal dep changed or fallback)
    - "EXEMPT"  — skip SIT entirely (PM-only change)
    - "targeted"— run only the slice
    """
    # Safety fallback: no changed repos → BROAD (never under-test)
    if not changed_repos:
        return "BROAD", [], ""

    try:
        manifest = _load_manifest(manifest_path)
    except Exception:
        return "BROAD", [], ""

    topology = _load_topology(topology_path)

    active = _active_repos(manifest)
    changed_set = set(changed_repos)

    # Universal-dep broadening: UAC or UTL changed → BROAD
    if changed_set & UNIVERSAL_DEPS:
        all_active_non_exempt = sorted(active - EXEMPT_REPOS)
        return "BROAD", all_active_non_exempt, ""

    # Filter changed to only non-exempt repos
    changed_filtered = changed_set - EXEMPT_REPOS

    # PM-only change (or no active service changed): EXEMPT
    if not changed_filtered:
        return "EXEMPT", [], ""

    # Build dep graph + compute slice
    reverse_graph = _build_reverse_dep_graph(manifest, topology, active)
    slice_with_deps = _transitive_dependents(changed_filtered, reverse_graph)

    # Final slice: transitive closure ∩ active, minus exempt
    final_slice = sorted((slice_with_deps & active) - EXEMPT_REPOS)

    # Safety: if slice is suspiciously small (0 after filtering), fall back to BROAD
    if not final_slice:
        return "BROAD", [], ""

    pytest_filter = _build_pytest_filter(set(final_slice))
    return "targeted", final_slice, pytest_filter


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compute the SIT repo-slice for a smoke-test-gate run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--changed",
        nargs="+",
        metavar="REPO",
        default=[],
        help="Repos that changed since last main promotion.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="Path to workspace-manifest.json (default: ../unified-trading-pm/workspace-manifest.json).",
    )
    parser.add_argument(
        "--topology",
        type=Path,
        default=DEFAULT_TOPOLOGY_PATH,
        help="Path to configs/runtime-topology.yaml (default: ../unified-trading-pm/configs/runtime-topology.yaml).",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        default=False,
        help="Emit only the JSON slice list (for testing/piping), not the env-var lines.",
    )

    args = parser.parse_args(argv)

    # Extract typed values from Namespace (argparse returns Any; use getattr + cast).
    _changed_raw = cast(object, getattr(args, "changed", []))
    changed_repos: list[str] = cast(list[str], _changed_raw) if isinstance(_changed_raw, list) else []

    _manifest_raw = cast(object, getattr(args, "manifest", DEFAULT_MANIFEST_PATH))
    manifest_path: Path = _manifest_raw if isinstance(_manifest_raw, Path) else DEFAULT_MANIFEST_PATH

    _topology_raw = cast(object, getattr(args, "topology", DEFAULT_TOPOLOGY_PATH))
    topology_path: Path = _topology_raw if isinstance(_topology_raw, Path) else DEFAULT_TOPOLOGY_PATH

    _json_only_raw = cast(object, getattr(args, "json_only", False))
    json_only: bool = _json_only_raw if isinstance(_json_only_raw, bool) else False

    mode, slice_repos, pytest_filter = compute_slice(
        changed_repos=changed_repos,
        manifest_path=manifest_path,
        topology_path=topology_path,
    )

    if json_only:
        print(json.dumps(slice_repos))
        return

    # Emit env-style key=value lines for GitHub Actions / shell consumption.
    print(f"SLICE_MODE={mode}")
    print(f"SLICE_REPOS_JSON={json.dumps(slice_repos)}")
    if pytest_filter:
        print(f"PYTEST_FILTER={pytest_filter}")
    else:
        print("PYTEST_FILTER=")

    # Human-readable summary on stderr
    if mode == "BROAD":
        print(f"[sit-slice] BROAD mode — full SIT (changed: {changed_repos or 'fallback'})", file=sys.stderr)
    elif mode == "EXEMPT":
        print(f"[sit-slice] EXEMPT — PM-only change ({changed_repos}), SIT not triggered", file=sys.stderr)
    else:
        print(
            f"[sit-slice] targeted — {len(slice_repos)} repos: {slice_repos}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
