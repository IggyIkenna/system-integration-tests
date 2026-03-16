"""Unit tests for repo_manager discovery and context loading."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from system_integration_tests.audit.repo_manager import (
    RepoContext,
    discover_repos,
    get_repo_context,
)


def _write_manifest(manifest_dir: Path, repos: dict[str, dict[str, object]]) -> Path:
    """Helper to write a minimal workspace manifest."""
    manifest = {"repos": repos}
    manifest_path = manifest_dir / "workspace-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


class TestDiscoverRepos:
    def test_basic_discovery(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            {
                "repo-a": {"tier": "T0", "ci_status": "VALIDATED", "dependencies": []},
                "repo-b": {"tier": "T1", "ci_status": "BASELINE_RECORDED", "dependencies": ["repo-a"]},
            },
        )
        repos = discover_repos(str(path))
        assert len(repos) == 2
        names = {r["name"] for r in repos}
        assert names == {"repo-a", "repo-b"}

    def test_repo_context_fields(self, tmp_path: Path) -> None:
        path = _write_manifest(
            tmp_path,
            {
                "my-service": {
                    "tier": "T3",
                    "ci_status": "VALIDATED",
                    "dependencies": ["unified-trading-library"],
                },
            },
        )
        repos = discover_repos(str(path))
        assert len(repos) == 1
        ctx = repos[0]
        assert ctx["name"] == "my-service"
        assert ctx["tier"] == "T3"
        assert ctx["ci_status"] == "VALIDATED"
        assert ctx["dependencies"] == ["unified-trading-library"]
        assert ctx["path"].endswith("my-service")

    def test_dict_dependencies(self, tmp_path: Path) -> None:
        """Dependencies can be list of dicts with 'name' key."""
        path = _write_manifest(
            tmp_path,
            {
                "svc": {
                    "tier": "T3",
                    "ci_status": "VALIDATED",
                    "dependencies": [
                        {"name": "unified-trading-library"},
                        {"name": "unified-config-interface"},
                    ],
                },
            },
        )
        repos = discover_repos(str(path))
        assert repos[0]["dependencies"] == ["unified-trading-library", "unified-config-interface"]

    def test_mixed_dependencies(self, tmp_path: Path) -> None:
        """String and dict dependencies can be mixed (9 repos use plain strings)."""
        path = _write_manifest(
            tmp_path,
            {
                "svc": {
                    "tier": "T3",
                    "ci_status": "VALIDATED",
                    "dependencies": [
                        "unified-trading-library",
                        {"name": "unified-config-interface"},
                    ],
                },
            },
        )
        repos = discover_repos(str(path))
        assert repos[0]["dependencies"] == ["unified-trading-library", "unified-config-interface"]

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            discover_repos("/nonexistent/manifest.json")

    def test_missing_repos_key(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'repos' key"):
            discover_repos(str(path))

    def test_repos_not_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text('{"repos": []}', encoding="utf-8")
        with pytest.raises(ValueError, match="must be a dict"):
            discover_repos(str(path))

    def test_empty_repos(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path, {})
        repos = discover_repos(str(path))
        assert repos == []

    def test_missing_fields_default(self, tmp_path: Path) -> None:
        path = _write_manifest(tmp_path, {"minimal-repo": {}})
        repos = discover_repos(str(path))
        assert repos[0]["tier"] == "unknown"
        assert repos[0]["ci_status"] == "unknown"
        assert repos[0]["dependencies"] == []


class TestGetRepoContext:
    def test_found(self) -> None:
        repos: list[RepoContext] = [
            RepoContext(name="a", path="/a", tier="T0", ci_status="VALIDATED", dependencies=[]),
            RepoContext(name="b", path="/b", tier="T1", ci_status="VALIDATED", dependencies=["a"]),
        ]
        ctx = get_repo_context("b", repos)
        assert ctx["name"] == "b"
        assert ctx["dependencies"] == ["a"]

    def test_not_found(self) -> None:
        repos: list[RepoContext] = [
            RepoContext(name="a", path="/a", tier="T0", ci_status="VALIDATED", dependencies=[]),
        ]
        with pytest.raises(KeyError, match="not found"):
            get_repo_context("nonexistent", repos)
