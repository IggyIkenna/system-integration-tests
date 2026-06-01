"""Repo discovery and context loading from workspace-manifest.json.

Reads the canonical workspace manifest and produces typed RepoContext dicts
for each repo, used by the audit agent to know which repos to check and
what tier/status each has.

Usage::

    from system_integration_tests.audit.repo_manager import discover_repos, get_repo_context

    repos = discover_repos("/path/to/workspace-manifest.json")
    ctx = get_repo_context("instruments-service", repos)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class RepoContext(TypedDict):  # CORRECT-LOCAL — test-harness infrastructure type, not a domain schema
    """Typed dict describing a single repo from the workspace manifest."""

    name: str
    path: str
    tier: str
    ci_status: str
    dependencies: list[str]


def discover_repos(manifest_path: str) -> list[RepoContext]:
    """Discover all repos from the workspace manifest.

    Args:
        manifest_path: Absolute path to workspace-manifest.json.

    Returns:
        List of RepoContext for every repo in the manifest.

    Raises:
        FileNotFoundError: If the manifest does not exist.
        ValueError: If the manifest is malformed.
    """
    path = Path(manifest_path)
    if not path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest_text = path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    repos_section = manifest.get("repos")
    if repos_section is None:
        raise ValueError(f"Manifest missing 'repos' key: {manifest_path}")

    if not isinstance(repos_section, dict):
        raise ValueError(f"Manifest 'repos' must be a dict, got {type(repos_section).__name__}")

    workspace_root = path.parent
    results: list[RepoContext] = []

    for repo_name, repo_data in repos_section.items():
        if not isinstance(repo_data, dict):
            logger.warning("Skipping non-dict repo entry: %s", repo_name)
            continue

        tier = str(repo_data.get("tier", "unknown"))
        ci_status = str(repo_data.get("ci_status", "unknown"))

        # Dependencies can be a list of strings or list of dicts with "name" key
        raw_deps = repo_data.get("dependencies", [])
        deps: list[str] = []
        if isinstance(raw_deps, list):
            for dep in raw_deps:
                if isinstance(dep, str):
                    deps.append(dep)
                elif isinstance(dep, dict):
                    dep_name = dep.get("name", "")
                    if dep_name:
                        deps.append(str(dep_name))

        repo_path = str(workspace_root / repo_name)

        results.append(
            RepoContext(
                name=repo_name,
                path=repo_path,
                tier=tier,
                ci_status=ci_status,
                dependencies=deps,
            )
        )

    logger.info("Discovered %d repos from manifest", len(results))
    return results


def get_repo_context(repo_name: str, repos: list[RepoContext]) -> RepoContext:
    """Look up a specific repo by name.

    Args:
        repo_name: The repo name to find.
        repos: List of RepoContext from discover_repos().

    Returns:
        The matching RepoContext.

    Raises:
        KeyError: If the repo is not in the list.
    """
    for repo in repos:
        if repo["name"] == repo_name:
            return repo
    available = ", ".join(r["name"] for r in repos[:10])
    raise KeyError(f"Repo {repo_name!r} not found. Available (first 10): {available}")
