"""
Regression test: `full-workspace-sit.yml`'s stamping-loop branch guard must accept a detached
HEAD that is verifiably live-defi-rollout (or an ancestor of it), not just a literal branch-name
match.

Bug (found live 2026-08-06, sit_stamp_skipped_on_detached_head_pinned_sha_2026_08_06.md): the
promote gate's SIT-on-LDR re-check dispatch (`ldr_to_main_fleet_promote.sh` STEP 5) pins this
job's clone of the target repo to an EXACT commit via `git checkout <sha>` (see the "pinning $r to
dispatched sha" step above in this same workflow) — that commit is a perfectly legitimate
live-defi-rollout commit (it is precisely the LDR_SHA the promote gate itself computed and asked
to be re-validated), but `git checkout <sha>` puts the clone in DETACHED HEAD state, so
`git rev-parse --abbrev-ref HEAD` returns the literal string "HEAD", not "live-defi-rollout". The
old guard did a literal `[ "$BR" != "live-defi-rollout" ]` comparison, so it unconditionally
skipped the stamp for this case every time — confirmed live in run 31110890960: SIT passed for
alerting-service, then "skip alerting-service (on 'HEAD', not live-defi-rollout)" — creating an
infinite promote-gate loop (SIT passes -> stamp skipped -> gate re-dispatches SIT -> forever).

The real safety net against stamping a genuinely WRONG tree is the CONSUMER's tree-equality check
(unified-trading-pm/scripts/cicd/ldr_to_main_fleet_promote.sh: `sit_validated_tree == LDR_TREE`,
an exact SHA-256 tree comparison, independent of branch names) — this local guard is only
defense-in-depth. The fix accepts a detached HEAD whose commit is live-defi-rollout's tip itself,
OR a verified ancestor of it (git merge-base --is-ancestor); a clone that genuinely fell back to
`main` still checks out a NAMED branch ("main"), so it is unaffected and still correctly rejected.

This EXTRACTS the REAL "Stamp SIT_VALIDATED + LDR tree" step's `run:` script from the live
workflow (not a replica) via PyYAML, slices out just the self-contained branch-guard sub-block
(bounded between two anchors that exist on both sides of it, unchanged by this fix), and executes
that exact extracted bash against real temp git repos in each of the five states the guard must
tell apart.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.abbreviated_sit

_GUARD_START_ANCHOR = 'BR="$(git -C "$r" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"'
# NOTE: this must NOT be a substring of anything inside the guard block itself (e.g. plain `SHA="`
# collides with the guard's own `CUR_SHA="..."` / `LDR_REF_SHA="..."` lines) — TREE= is unique and
# is the line immediately following the guard's closing `fi` in the real workflow step.
_GUARD_END_ANCHOR = 'TREE="$(git -C "$r" rev-parse \'HEAD^{tree}\' 2>/dev/null || echo "")"'


def _workflow_path() -> Path:
    # This file lives at: system-integration-tests/tests/abbreviated/<this file>.py
    return Path(__file__).parents[2] / ".github" / "workflows" / "full-workspace-sit.yml"


def _extract_stamp_step_run() -> str:
    wf = _workflow_path()
    assert wf.is_file(), f"full-workspace-sit.yml not found at {wf}"
    doc = yaml.safe_load(wf.read_text())
    steps = doc["jobs"]["cross-repo-invariants"]["steps"]
    for step in steps:
        if step.get("name", "").startswith("Stamp SIT_VALIDATED"):
            run_text = step["run"]
            assert isinstance(run_text, str)
            return run_text
    raise AssertionError("'Stamp SIT_VALIDATED + LDR tree' step not found")


def _extract_branch_guard() -> str:
    """Slice out just the self-contained branch-name/detached-HEAD guard sub-block."""
    run_text = _extract_stamp_step_run()
    start = run_text.index(_GUARD_START_ANCHOR)
    end = run_text.index(_GUARD_END_ANCHOR, start)
    assert end > start, "guard end anchor not found after start anchor"
    return run_text[start:end]


def test_guard_carries_the_detached_head_acceptance_path() -> None:
    """Structural anchor: a future edit that drops the ancestry check must fail here."""
    guard = _extract_branch_guard()
    assert 'elif [ "$BR" = "HEAD" ]; then' in guard, "stamp guard is missing the detached-HEAD branch:\n" + guard
    assert "merge-base --is-ancestor" in guard
    assert "ON_LDR=false" in guard and "ON_LDR=true" in guard


def _run_guard(repo_dir: Path) -> tuple[int, str, bool]:
    """Run the extracted guard snippet with $r=<repo_dir>, in a one-iteration `for` loop so the
    guard's own `continue` behaves exactly as it does in the real (unextracted) workflow loop.

    Returns (exit_code, stdout+stderr, stamp_reached) where stamp_reached is True iff the guard
    fell through to "would proceed to stamp" (the sentinel echo after the extracted block, never
    reached if `continue` fired).
    """
    guard = _extract_branch_guard()
    script = f"""
set -uo pipefail
for r in "{repo_dir}"; do
{guard}
  echo "STAMP_REACHED"
done
"""
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    return proc.returncode, output, "STAMP_REACHED" in output


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(cwd),
        },
    )
    return proc.stdout.strip()


def test_normal_branch_checkout_is_accepted(tmp_path: Path) -> None:
    """The common case: `$r` is checked out on the named branch live-defi-rollout."""
    repo = tmp_path / "alerting-service"
    repo.mkdir()
    _git("init", "-q", "-b", "live-defi-rollout", cwd=repo)
    (repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-q", "-m", "c1", cwd=repo)

    rc, output, stamp_reached = _run_guard(repo)
    assert rc == 0, output
    assert stamp_reached, f"expected the normal live-defi-rollout checkout to be accepted:\n{output}"
    assert "skip" not in output.lower()


def test_fallback_to_main_is_still_rejected(tmp_path: Path) -> None:
    """The guard's original purpose: a clone that fell back to `main` must stay skipped."""
    repo = tmp_path / "alerting-service"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    (repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-q", "-m", "c1", cwd=repo)

    rc, output, stamp_reached = _run_guard(repo)
    assert rc == 0, output
    assert not stamp_reached, f"a main-fallback clone must NOT be stamped:\n{output}"
    assert "skip" in output.lower()
    assert "not verified as live-defi-rollout" in output


def test_detached_head_at_exact_ldr_tip_is_accepted(tmp_path: Path) -> None:
    """THE fix under test (fast path): the pinned SHA equals live-defi-rollout's tip at clone
    time (the common case — LDR was quiescent between the promote gate's read and this clone)."""
    repo = tmp_path / "alerting-service"
    repo.mkdir()
    _git("init", "-q", "-b", "live-defi-rollout", cwd=repo)
    (repo / "f.txt").write_text("x")
    _git("add", "f.txt", cwd=repo)
    _git("commit", "-q", "-m", "c1", cwd=repo)
    tip_sha = _git("rev-parse", "HEAD", cwd=repo)

    # Simulate the "pinning $r to dispatched sha" checkout step: detach HEAD at that same commit.
    _git("checkout", "-q", tip_sha, cwd=repo)
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "HEAD"

    rc, output, stamp_reached = _run_guard(repo)
    assert rc == 0, output
    assert stamp_reached, f"a detached HEAD exactly at live-defi-rollout's own tip must be accepted:\n{output}"


def test_detached_head_at_ancestor_after_ldr_churn_is_accepted(tmp_path: Path) -> None:
    """THE fix under test (merge-base path): LDR advanced (fleet churn) between the promote
    gate's frozen-head read (pinned to an OLDER commit, c2) and this job's own clone of
    live-defi-rollout (which lands on the NEWER tip, c3) — c2 is still a legitimate
    live-defi-rollout commit (an ancestor of c3), and must be accepted."""
    origin_bare = tmp_path / "origin.git"
    origin_bare.mkdir()
    _git("init", "-q", "--bare", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "live-defi-rollout", cwd=seed)
    _git("remote", "add", "origin", str(origin_bare), cwd=seed)
    (seed / "f.txt").write_text("c1")
    _git("add", "f.txt", cwd=seed)
    _git("commit", "-q", "-m", "c1", cwd=seed)
    _git("push", "-q", "-u", "origin", "live-defi-rollout", cwd=seed)

    (seed / "f.txt").write_text("c2")
    _git("add", "f.txt", cwd=seed)
    _git("commit", "-q", "-m", "c2 (the pinned sha)", cwd=seed)
    pinned_sha = _git("rev-parse", "HEAD", cwd=seed)
    _git("push", "-q", "origin", "live-defi-rollout", cwd=seed)

    (seed / "f.txt").write_text("c3")
    _git("add", "f.txt", cwd=seed)
    _git("commit", "-q", "-m", "c3 (LDR moved on before the SIT clone)", cwd=seed)
    _git("push", "-q", "origin", "live-defi-rollout", cwd=seed)

    # This job's own clone of live-defi-rollout lands on the ALREADY-ADVANCED tip (c3) — mirrors
    # the real "Clone all active repos" step cloning $DEP_BRANCH's live tip.
    repo = tmp_path / "alerting-service"
    _git("clone", "-q", "-b", "live-defi-rollout", str(origin_bare), str(repo), cwd=tmp_path)
    assert _git("rev-parse", "HEAD", cwd=repo) != pinned_sha

    # Then the "pinning $r to dispatched sha" step fetches + checks out the OLDER pinned commit.
    _git("fetch", "-q", "origin", pinned_sha, cwd=repo)
    _git("checkout", "-q", pinned_sha, cwd=repo)
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "HEAD"

    rc, output, stamp_reached = _run_guard(repo)
    assert rc == 0, output
    assert stamp_reached, (
        f"a detached HEAD at an ANCESTOR of the (moved-on) live-defi-rollout tip must be "
        f"accepted via merge-base --is-ancestor:\n{output}"
    )


def test_detached_head_at_unrelated_commit_is_rejected(tmp_path: Path) -> None:
    """Proves the fix does not just accept ANY detached HEAD — an unrelated commit with no
    shared history with live-defi-rollout must still be rejected."""
    origin_bare = tmp_path / "origin.git"
    origin_bare.mkdir()
    _git("init", "-q", "--bare", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "live-defi-rollout", cwd=seed)
    _git("remote", "add", "origin", str(origin_bare), cwd=seed)
    (seed / "f.txt").write_text("ldr")
    _git("add", "f.txt", cwd=seed)
    _git("commit", "-q", "-m", "ldr commit", cwd=seed)
    _git("push", "-q", "-u", "origin", "live-defi-rollout", cwd=seed)

    repo = tmp_path / "alerting-service"
    _git("clone", "-q", "-b", "live-defi-rollout", str(origin_bare), str(repo), cwd=tmp_path)

    # An orphan commit sharing NO history with live-defi-rollout (e.g. a stray local commit —
    # not something the real pin step would produce, but exactly what the guard must still reject
    # if it ever somehow occurred).
    _git("checkout", "-q", "--orphan", "unrelated", cwd=repo)
    (repo / "g.txt").write_text("unrelated")
    _git("add", "g.txt", cwd=repo)
    _git("commit", "-q", "-m", "unrelated history", cwd=repo)
    unrelated_sha = _git("rev-parse", "HEAD", cwd=repo)
    _git("checkout", "-q", unrelated_sha, cwd=repo)
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "HEAD"

    rc, output, stamp_reached = _run_guard(repo)
    assert rc == 0, output
    assert not stamp_reached, (
        f"a detached HEAD with NO ancestry relationship to live-defi-rollout must NOT be stamped:\n{output}"
    )
    assert "skip" in output.lower()
