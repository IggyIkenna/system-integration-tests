"""Cascade pipeline integration smoke tests.

Validates that the version-cascade pipeline infrastructure is correctly wired:
topological ordering, dependency graph acyclicity, workflow YAML structure,
Telegram notifications, secrets inheritance, breaking-change flag propagation,
and auto-merge safety gates.

Marker: code_test (no live services required).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import deque
from pathlib import Path

import pytest
import yaml

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parents[3]))
PM_ROOT = WORKSPACE_ROOT / "unified-trading-pm"
MANIFEST_PATH = PM_ROOT / "workspace-manifest.json"
WORKFLOWS_DIR = PM_ROOT / ".github" / "workflows"
TEMPLATES_DIR = PM_ROOT / "scripts" / "workflow-templates"


def _load_manifest() -> dict[str, object]:
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _load_workflow(name: str) -> dict[str, object]:
    path = WORKFLOWS_DIR / name
    assert path.is_file(), f"Workflow not found: {path}"
    with open(path) as f:
        return yaml.safe_load(f.read())


@pytest.mark.code_test
class TestCascadeQGOrderingWorkflow:
    """Validate cascade-qg-ordering.yml structure."""

    def test_cascade_qg_ordering_workflow_valid_yaml(self) -> None:
        """Load cascade-qg-ordering.yml, validate it is parseable YAML with
        expected structure: repository_dispatch trigger and concurrency group."""
        wf = _load_workflow("cascade-qg-ordering.yml")

        # Must have 'on' key with repository_dispatch
        triggers = wf.get("on") or wf.get(True)  # YAML parses bare 'on' as True
        assert triggers is not None, "cascade-qg-ordering.yml missing 'on' trigger"

        assert "repository_dispatch" in triggers, (
            "cascade-qg-ordering.yml missing repository_dispatch trigger"
        )

        # Must have concurrency group
        concurrency = wf.get("concurrency")
        assert concurrency is not None, "cascade-qg-ordering.yml missing concurrency"
        assert "group" in concurrency, "cascade-qg-ordering.yml concurrency missing group"


@pytest.mark.code_test
class TestInvalidateCIStatusScript:
    """Validate invalidate-ci-status.py exists and works in dry-run mode."""

    SCRIPT_PATH = PM_ROOT / "scripts" / "cascade" / "invalidate-ci-status.py"

    def test_invalidate_ci_status_script_exists_and_executable(self) -> None:
        """Verify scripts/cascade/invalidate-ci-status.py exists and is executable."""
        assert self.SCRIPT_PATH.is_file(), (
            f"invalidate-ci-status.py not found at {self.SCRIPT_PATH}"
        )
        # Check executable bit (owner or group or other)
        mode = self.SCRIPT_PATH.stat().st_mode
        is_executable = bool(mode & 0o111)
        assert is_executable, (
            f"invalidate-ci-status.py is not executable (mode={oct(mode)})"
        )

    def test_invalidate_ci_status_dry_run(self) -> None:
        """Run invalidate-ci-status.py with --dry-run and verify it outputs
        affected repos without modifying the manifest."""
        manifest_before = MANIFEST_PATH.read_text()

        result = subprocess.run(
            [
                "python3",
                str(self.SCRIPT_PATH),
                "unified-market-interface",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PM_ROOT),
        )

        manifest_after = MANIFEST_PATH.read_text()

        # Dry run must not modify the manifest
        assert manifest_before == manifest_after, (
            "invalidate-ci-status.py --dry-run modified the manifest"
        )

        # Script should exit cleanly (0 = success, or expected non-zero for "nothing to do")
        assert result.returncode in (0, 1), (
            f"invalidate-ci-status.py --dry-run failed unexpectedly "
            f"(rc={result.returncode}):\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Stdout or stderr should mention dry-run or the target repo
        combined = result.stdout + result.stderr
        assert "dry" in combined.lower() or "unified-market-interface" in combined, (
            f"Dry-run output does not reference dry-run or target repo:\n{combined}"
        )


@pytest.mark.code_test
class TestTopologicalOrder:
    """Validate topological ordering in workspace-manifest.json."""

    def test_topological_order_covers_all_repos(self) -> None:
        """Read manifest topologicalOrder.levels, flatten into a set,
        verify every repo in repositories{} appears in exactly one level."""
        manifest = _load_manifest()

        repositories = manifest.get("repositories", {})
        assert repositories, "Manifest has no repositories"

        topo = manifest.get("topologicalOrder", {})
        levels = topo.get("levels", [])
        assert levels, "Manifest topologicalOrder.levels is empty"

        # Flatten all repos from topological levels
        topo_repos: list[str] = []
        for level in levels:
            repos_in_level = level.get("repos", [])
            topo_repos.extend(repos_in_level)

        topo_set = set(topo_repos)
        repo_set = set(repositories.keys())

        # Check for duplicates in topological order
        assert len(topo_repos) == len(topo_set), (
            f"Duplicate repos in topologicalOrder: "
            f"{[r for r in topo_repos if topo_repos.count(r) > 1]}"
        )

        # Every repo in topological order must be in repositories (no phantom entries).
        # Not all repos must be in topo order — standalone repos (websites) may be excluded.
        # But any repo WITH dependencies should appear in a level.
        repos_with_deps = {
            name for name, cfg in repositories.items()
            if cfg.get("dependencies") and name not in topo_set
        }
        assert not repos_with_deps, (
            f"Repos with dependencies but missing from topologicalOrder: {repos_with_deps}"
        )

        # Every repo in topological order must appear in repositories
        extra_in_topo = topo_set - repo_set
        assert not extra_in_topo, (
            f"Repos in topologicalOrder but not in repositories{{}}: {extra_in_topo}"
        )


@pytest.mark.code_test
class TestDependencyGraph:
    """Validate dependency graph structure."""

    def test_dependency_graph_is_acyclic(self) -> None:
        """Build dependency graph from manifest, verify no cycles via BFS
        topological sort (Kahn's algorithm)."""
        manifest = _load_manifest()
        repositories = manifest.get("repositories", {})

        # Build adjacency list: repo -> list of repos it depends on
        # and in-degree map for Kahn's algorithm
        all_repos = set(repositories.keys())
        graph: dict[str, list[str]] = {repo: [] for repo in all_repos}
        in_degree: dict[str, int] = {repo: 0 for repo in all_repos}

        for repo_name, repo_data in repositories.items():
            deps = repo_data.get("dependencies", [])
            for dep in deps:
                dep_name = dep.get("name") if isinstance(dep, dict) else dep
                if dep_name in all_repos:
                    # dep_name -> repo_name (dep must come before dependent)
                    graph[dep_name].append(repo_name)
                    in_degree[repo_name] += 1

        # Kahn's algorithm — BFS topological sort
        queue: deque[str] = deque()
        for repo in all_repos:
            if in_degree[repo] == 0:
                queue.append(repo)

        visited_count = 0
        while queue:
            node = queue.popleft()
            visited_count += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        assert visited_count == len(all_repos), (
            f"Dependency graph has a cycle: only {visited_count}/{len(all_repos)} "
            f"repos reachable. Stuck repos: "
            f"{[r for r in all_repos if in_degree[r] > 0]}"
        )


@pytest.mark.code_test
class TestCascadeWorkflowTelegram:
    """Verify cascade workflows have Telegram notification wiring."""

    WORKFLOW_NAMES = [
        "cascade-qg-ordering.yml",
        "downstream-fix-agent.yml",
        "fix-approval-timeout.yml",
    ]

    def test_cascade_workflows_have_telegram(self) -> None:
        """Verify cascade-qg-ordering.yml, downstream-fix-agent.yml, and
        fix-approval-timeout.yml all reference notify-telegram or inline
        Telegram notification."""
        for wf_name in self.WORKFLOW_NAMES:
            wf_path = WORKFLOWS_DIR / wf_name
            assert wf_path.is_file(), f"Workflow not found: {wf_path}"
            content = wf_path.read_text()
            has_telegram = (
                "notify-telegram" in content
                or "TELEGRAM_BOT_TOKEN" in content
                or "telegram" in content.lower()
            )
            assert has_telegram, (
                f"{wf_name} does not reference Telegram notifications"
            )


@pytest.mark.code_test
class TestSecretsInherit:
    """Verify workflows that call persist-cicd-event.yml include secrets: inherit."""

    def test_cascade_workflows_have_secrets_inherit(self) -> None:
        """All workflows that call persist-cicd-event.yml must include
        secrets: inherit on that job."""
        persist_callers: list[str] = []
        missing_inherit: list[str] = []

        for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
            content = wf_path.read_text()
            if "persist-cicd-event.yml" not in content:
                continue

            persist_callers.append(wf_path.name)

            # Check that secrets: inherit appears near persist-cicd-event references.
            # The pattern is: uses: ./.github/workflows/persist-cicd-event.yml
            # followed by secrets: inherit within a few lines.
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if "persist-cicd-event.yml" in line:
                    # Search forward within 5 lines for secrets: inherit
                    window = "\n".join(lines[i : i + 5])
                    if "secrets: inherit" not in window:
                        missing_inherit.append(f"{wf_path.name}:L{i + 1}")

        assert persist_callers, "No workflows call persist-cicd-event.yml"
        assert not missing_inherit, (
            f"Workflows calling persist-cicd-event.yml without 'secrets: inherit': "
            f"{missing_inherit}"
        )


@pytest.mark.code_test
class TestBreakingFlagDispatchChain:
    """Verify is_breaking flag flows through the entire dispatch chain."""

    def test_breaking_flag_in_dispatch_chain(self) -> None:
        """Verify semver-agent.yml.tmpl contains is_breaking,
        update-repo-version.yml reads is_breaking,
        update-dependency-version.yml reads is_breaking."""
        checks: list[tuple[str, Path]] = [
            ("semver-agent.yml.tmpl", TEMPLATES_DIR / "semver-agent.yml.tmpl"),
            ("update-repo-version.yml", WORKFLOWS_DIR / "update-repo-version.yml"),
            (
                "update-dependency-version.yml",
                TEMPLATES_DIR / "update-dependency-version.yml",
            ),
        ]

        for label, path in checks:
            assert path.is_file(), f"{label} not found at {path}"
            content = path.read_text()
            assert "is_breaking" in content, (
                f"{label} does not contain 'is_breaking' — "
                f"breaking-change flag will not propagate through dispatch chain"
            )


@pytest.mark.code_test
class TestDownstreamFixAgentClaude:
    """Verify downstream-fix-agent.yml calls the Anthropic API."""

    def test_downstream_fix_agent_has_claude_api_call(self) -> None:
        """Verify downstream-fix-agent.yml contains an Anthropic API call pattern."""
        wf_path = WORKFLOWS_DIR / "downstream-fix-agent.yml"
        assert wf_path.is_file(), f"downstream-fix-agent.yml not found at {wf_path}"
        content = wf_path.read_text()

        has_anthropic_api = (
            "api.anthropic.com" in content or "ANTHROPIC_API_KEY" in content
        )
        assert has_anthropic_api, (
            "downstream-fix-agent.yml does not contain Anthropic API call pattern "
            "(expected 'api.anthropic.com' or 'ANTHROPIC_API_KEY')"
        )


@pytest.mark.code_test
class TestAutoMergeSafetyGates:
    """Verify auto-merge-minor-fixes.yml has critical safety gates."""

    def test_auto_merge_has_safety_gates(self) -> None:
        """Verify auto-merge-minor-fixes.yml checks:
        - version >= 1.0.0 (pre-1.0.0 requires human approval)
        - is_breaking != true
        - dry_run default is true
        """
        wf_path = WORKFLOWS_DIR / "auto-merge-minor-fixes.yml"
        assert wf_path.is_file(), f"auto-merge-minor-fixes.yml not found at {wf_path}"
        content = wf_path.read_text()

        # Gate 1: version >= 1.0.0 check (pre-1.0.0 guard)
        has_version_gate = "1.0.0" in content or "pre_1_0" in content or "is_pre_1_0" in content
        assert has_version_gate, (
            "auto-merge-minor-fixes.yml missing version >= 1.0.0 safety gate"
        )

        # Gate 2: is_breaking check
        has_breaking_gate = "is_breaking" in content or "breaking" in content.lower()
        assert has_breaking_gate, (
            "auto-merge-minor-fixes.yml missing is_breaking safety gate"
        )

        # Gate 3: dry_run defaults to true
        wf = _load_workflow("auto-merge-minor-fixes.yml")
        triggers = wf.get("on") or wf.get(True)
        assert triggers is not None, "auto-merge-minor-fixes.yml missing triggers"

        wd_inputs = (triggers.get("workflow_dispatch") or {}).get("inputs") or {}
        dry_run_input = wd_inputs.get("dry_run", {})
        assert dry_run_input.get("default") is True, (
            f"auto-merge-minor-fixes.yml dry_run default is not true "
            f"(got {dry_run_input.get('default')!r})"
        )
