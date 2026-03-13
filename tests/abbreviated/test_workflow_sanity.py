"""
Abbreviated SIT: Workflow YAML sanity checks across all repos in the workspace.

Validates without live GHA runners:
- Key workflow files are syntactically valid YAML
- workflow_run trigger references match actual workflow names in the same repo
- Required field shapes are present (on: triggers have expected structure)

Target runtime: <60s total. Reads local workflow files only (no gh api calls in abbreviated mode).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest
import yaml

pytestmark = pytest.mark.abbreviated_sit

# Type alias for a parsed YAML workflow document
WorkflowDoc = dict[str, object]


def _workspace_root() -> Path:
    """Return workspace root from env var or infer from this file's location.

    File lives at: <workspace_root>/system-integration-tests/tests/abbreviated/test_workflow_sanity.py
    So parents[4] = workspace_root.
    """
    env_root = os.environ.get("UNIFIED_TRADING_WORKSPACE_ROOT", "")
    if env_root:
        return Path(env_root)
    return Path(__file__).parents[4]


def _collect_repo_dirs(workspace_root: Path) -> list[Path]:
    """Return all direct subdirectories that look like git repos with .github/workflows/."""
    skip_names = {"node_modules", "archive", "bash", "--ui-only"}
    repos: list[Path] = []
    for candidate in sorted(workspace_root.iterdir()):
        if not candidate.is_dir():
            continue
        if candidate.name.startswith(".") or candidate.name in skip_names:
            continue
        if (candidate / ".github" / "workflows").is_dir():
            repos.append(candidate)
    return repos


def _parse_workflow_file(wf_file: Path) -> WorkflowDoc | None:
    """Parse a single workflow YAML file. Return typed dict or None on error/non-dict.

    yaml.safe_load() is typed as returning Any; we narrow the type here so
    callers receive WorkflowDoc | None without Any leaking into their scope.

    PyYAML (YAML 1.1) may parse bare 'on:' keys as Python True.  We normalise
    this to the string key "on" before returning so callers never see a bool key.
    """
    try:
        # cast(object, ...) prevents the Any return type of yaml.safe_load from
        # propagating into callers under basedpyright strict reportAny mode.
        parsed: object = cast(object, yaml.safe_load(wf_file.read_text()))
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    # Normalise PyYAML's bool True key ('on' → True in YAML 1.1) to string "on".
    # Cast to dict[object, object] first so iteration has known (not Unknown) types.
    raw_dict = cast(dict[object, object], parsed)
    normalised: WorkflowDoc = {}
    for raw_key, val in raw_dict.items():
        str_key: str = "on" if raw_key is True else str(raw_key)
        normalised[str_key] = val
    return normalised


def _load_workflow_names(repo_dir: Path) -> dict[str, str]:
    """Return {workflow_file_name: workflow_name} for all valid YAML workflows in a repo.

    Only includes workflows that parse successfully and have a top-level 'name' field.
    """
    workflows_dir = repo_dir / ".github" / "workflows"
    names: dict[str, str] = {}
    for wf_file in sorted(workflows_dir.glob("*.yml")):
        content = _parse_workflow_file(wf_file)
        if content is None:
            continue
        wf_name = content.get("name")
        if isinstance(wf_name, str):
            names[wf_file.name] = wf_name
    return names


def _get_on_block(content: WorkflowDoc) -> WorkflowDoc | None:
    """Extract the 'on:' trigger block.

    Keys are already normalised to strings by _parse_workflow_file (bool True → "on"),
    so we always look up the string key "on".
    """
    on_val = content.get("on")
    return cast(WorkflowDoc, on_val) if isinstance(on_val, dict) else None


def _has_on_trigger(content: WorkflowDoc) -> bool:
    """Return True if the workflow has an 'on:' trigger.

    Keys are already normalised to strings by _parse_workflow_file.
    """
    return "on" in content


def _workflow_run_broken_refs(
    wf_file: Path,
    repo_name: str,
    all_workflow_names: set[str],
) -> list[str]:
    """Return broken workflow_run reference strings for a single workflow file."""
    content = _parse_workflow_file(wf_file)
    if content is None:
        return []
    on_block = _get_on_block(content)
    if on_block is None:
        return []
    workflow_run_raw = on_block.get("workflow_run")
    if not isinstance(workflow_run_raw, dict):
        return []
    workflow_run = cast(WorkflowDoc, workflow_run_raw)
    referenced_raw = workflow_run.get("workflows")
    if not isinstance(referenced_raw, list):
        return []
    broken: list[str] = []
    for ref_name in cast(list[object], referenced_raw):
        if isinstance(ref_name, str) and ref_name not in all_workflow_names:
            broken.append(
                f"{repo_name}/{wf_file.name}: references '{ref_name}' "
                f"but no workflow named '{ref_name}' found in "
                f"{repo_name}/.github/workflows/ (known: {sorted(all_workflow_names)!r})"
            )
    return broken


def _check_jobs(
    wf_file: Path,
    repo_name: str,
) -> tuple[str | None, str | None]:
    """Return (missing_jobs_msg, empty_jobs_msg) or (None, None) if both are fine."""
    content = _parse_workflow_file(wf_file)
    if content is None:
        return None, None
    jobs = content.get("jobs")
    label = f"{repo_name}/{wf_file.name}"
    if jobs is None:
        return label, None
    if not isinstance(jobs, dict):
        return None, label
    jobs_dict = cast(dict[str, object], jobs)
    if len(jobs_dict) == 0:
        return None, label
    return None, None


class TestWorkflowYAMLSyntax:
    """All .github/workflows/*.yml files across all local repos must parse as valid YAML."""

    def test_all_workflow_files_are_valid_yaml(self) -> None:
        """Parse every workflow YAML file found locally; collect and report all failures."""
        workspace_root = _workspace_root()
        repos = _collect_repo_dirs(workspace_root)

        if not repos:
            pytest.skip(f"No repos with .github/workflows/ found under {workspace_root}")

        failures: list[str] = []
        total_files = 0

        for repo_dir in repos:
            for wf_file in sorted((repo_dir / ".github" / "workflows").glob("*.yml")):
                total_files += 1
                try:
                    parsed: object = cast(object, yaml.safe_load(wf_file.read_text()))
                except yaml.YAMLError as exc:
                    failures.append(f"{repo_dir.name}/{wf_file.name}: {exc}")
                    continue
                if not isinstance(parsed, dict):
                    failures.append(f"{repo_dir.name}/{wf_file.name}: parsed to {type(parsed).__name__}, expected dict")

        assert total_files > 0, f"No workflow files found under {workspace_root}"
        assert not failures, f"{len(failures)} workflow file(s) failed YAML validation:\n" + "\n".join(
            f"  - {f}" for f in failures
        )

    def test_all_workflows_have_on_trigger(self) -> None:
        """Every workflow file must have an 'on:' trigger field."""
        workspace_root = _workspace_root()
        repos = _collect_repo_dirs(workspace_root)

        if not repos:
            pytest.skip(f"No repos with .github/workflows/ found under {workspace_root}")

        missing_on: list[str] = []

        for repo_dir in repos:
            for wf_file in sorted((repo_dir / ".github" / "workflows").glob("*.yml")):
                content = _parse_workflow_file(wf_file)
                if content is None:
                    continue
                if not _has_on_trigger(content):
                    missing_on.append(f"{repo_dir.name}/{wf_file.name}")

        assert not missing_on, f"{len(missing_on)} workflow file(s) missing 'on:' trigger:\n" + "\n".join(
            f"  - {f}" for f in missing_on
        )


class TestWorkflowRunTriggerConsistency:
    """workflow_run triggers must reference workflow names that exist in the same repo."""

    def test_workflow_run_references_exist(self) -> None:
        """For each workflow_run trigger, verify the referenced name exists in the repo."""
        workspace_root = _workspace_root()
        repos = _collect_repo_dirs(workspace_root)

        if not repos:
            pytest.skip(f"No repos with .github/workflows/ found under {workspace_root}")

        broken_refs: list[str] = []

        for repo_dir in repos:
            all_names = set(_load_workflow_names(repo_dir).values())
            for wf_file in sorted((repo_dir / ".github" / "workflows").glob("*.yml")):
                broken_refs.extend(_workflow_run_broken_refs(wf_file, repo_dir.name, all_names))

        assert not broken_refs, (
            f"{len(broken_refs)} workflow_run trigger reference(s) point to non-existent workflow names:\n"
            + "\n".join(f"  - {r}" for r in broken_refs)
        )


class TestWorkflowJobsStructure:
    """Workflow files must have a 'jobs:' section with at least one job."""

    def test_all_workflows_have_jobs(self) -> None:
        """Every non-trivial workflow must define at least one job."""
        workspace_root = _workspace_root()
        repos = _collect_repo_dirs(workspace_root)

        if not repos:
            pytest.skip(f"No repos with .github/workflows/ found under {workspace_root}")

        missing_jobs: list[str] = []
        empty_jobs: list[str] = []

        for repo_dir in repos:
            for wf_file in sorted((repo_dir / ".github" / "workflows").glob("*.yml")):
                missing, empty = _check_jobs(wf_file, repo_dir.name)
                if missing:
                    missing_jobs.append(missing)
                if empty:
                    empty_jobs.append(empty)

        assert not missing_jobs, f"{len(missing_jobs)} workflow file(s) missing 'jobs:' key:\n" + "\n".join(
            f"  - {f}" for f in missing_jobs
        )
        assert not empty_jobs, f"{len(empty_jobs)} workflow file(s) have an empty 'jobs:' section:\n" + "\n".join(
            f"  - {f}" for f in empty_jobs
        )
