"""
Regression test: `full-workspace-sit.yml`'s "Report SIT result to PM" step must no-op on a
cancelled run, not dispatch `sit-failed`.

Source: plans/active/ci_satellite_ao_dispatch_batch1_2026_07_26.md todo 13a;
plans/active/issues/sit_validated_tree_treadmill_blocks_breaking_promotes_2026_07_20.md
"Distinct sub-finding" (the same cancelled-run-has-no-informative-verdict bug class also present
in `.github/workflows/ldr-to-main-promote-fleet.yml`'s `sit-gate/fleet-green` derivation, fixed
separately in that repo).

Bug: `job.status` is one of success/failure/cancelled. The original step treated ANY non-success
status as `sit-failed` — so a run cancelled mid-flight (e.g. an overlapping dispatch superseded by
a newer one) reported a FALSE failure to `sit-unlock`, an uninformative verdict.

This EXTRACTS the REAL "Report SIT result to PM" step's `run:` script from the live workflow (not
a replica) via PyYAML, substitutes the GHA `${{ job.status }}` expression the same way the GitHub
Actions runner would before invoking the shell, and runs it with a stubbed `gh` that records every
invocation — proving: cancelled -> no dispatch at all; success -> sit-passed; failure -> sit-failed.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.abbreviated_sit


def _workflow_path() -> Path:
    # This file lives at: system-integration-tests/tests/abbreviated/<this file>.py
    return Path(__file__).parents[2] / ".github" / "workflows" / "full-workspace-sit.yml"


def _extract_report_step_run() -> str:
    wf = _workflow_path()
    assert wf.is_file(), f"full-workspace-sit.yml not found at {wf}"
    doc = yaml.safe_load(wf.read_text())
    steps = doc["jobs"]["cross-repo-invariants"]["steps"]
    for step in steps:
        if step.get("name") == "Report SIT result to PM (drives sit-unlock)":
            run_text = step["run"]
            assert isinstance(run_text, str)
            return run_text
    raise AssertionError("'Report SIT result to PM (drives sit-unlock)' step not found")


def test_report_step_carries_the_cancelled_noop_guard() -> None:
    """Structural anchor: a future edit that drops the cancelled-check must fail here."""
    run_text = _extract_report_step_run()
    assert '"${{ job.status }}" = "cancelled"' in run_text, (
        "Report SIT result to PM step is missing the cancelled-job no-op guard:\n" + run_text
    )
    assert "exit 0" in run_text


def _run_with_job_status(job_status: str) -> tuple[int, str, list[str]]:
    """Run the extracted step script with `${{ job.status }}` substituted, `gh` stubbed.

    Returns (exit_code, stdout+stderr, list of dispatch event_types the stub gh recorded).
    """
    run_text = _extract_report_step_run()
    # GHA substitutes `${{ job.status }}` with its literal value BEFORE the runner shell ever
    # sees the script — replicate that here rather than trying to interpret the expression.
    rendered = run_text.replace("${{ job.status }}", job_status)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        calls_log = tmp_path / "gh_calls.log"
        gh_stub = tmp_path / "gh"
        gh_stub.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> "{calls_log}"\n'
            'if [ "$1" = "api" ]; then exit 0; fi\n'
            'echo "gh stub: unexpected invocation: $*" >&2\n'
            "exit 1\n"
        )
        gh_stub.chmod(gh_stub.stat().st_mode | stat.S_IEXEC)

        env = dict(os.environ)
        env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
        env["GH_TOKEN"] = "dummy-token-not-real"

        proc = subprocess.run(
            ["bash", "-c", rendered],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        events: list[str] = []
        if calls_log.is_file():
            for line in calls_log.read_text().splitlines():
                if "event_type=" in line:
                    # e.g.: api repos/.../dispatches -X POST -f event_type=sit-passed
                    for tok in line.split():
                        if tok.startswith("event_type="):
                            events.append(tok.split("=", 1)[1])
        return proc.returncode, proc.stdout + proc.stderr, events


def test_cancelled_job_status_does_not_dispatch_any_event() -> None:
    """THE fix under test: a cancelled run must no-op, never report sit-failed."""
    rc, output, events = _run_with_job_status("cancelled")
    assert rc == 0, f"cancelled-job script must exit 0 (no-op), got rc={rc}:\n{output}"
    assert events == [], f"cancelled-job script must dispatch NO event, got: {events}\n{output}"
    assert "no informative verdict" in output.lower()


def test_success_job_status_dispatches_sit_passed() -> None:
    rc, output, events = _run_with_job_status("success")
    assert rc == 0, output
    assert events == ["sit-passed"], f"expected exactly one sit-passed dispatch, got: {events}\n{output}"


def test_failure_job_status_dispatches_sit_failed() -> None:
    rc, output, events = _run_with_job_status("failure")
    assert rc == 0, output
    assert events == ["sit-failed"], f"expected exactly one sit-failed dispatch, got: {events}\n{output}"
