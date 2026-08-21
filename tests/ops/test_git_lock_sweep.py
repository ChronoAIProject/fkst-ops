from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
SWEEP = ROOT / "ops" / "git_lock_sweep.py"


def lsof_probe(path: Path) -> Path:
    probe = path / "lsof-probe"
    probe.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "record = pathlib.Path(sys.argv[-1] + '.holder-pid')\n"
        "try:\n"
        "    pid = int(record.read_text(encoding='ascii'))\n"
        "    os.kill(pid, 0)\n"
        "except (OSError, ValueError):\n"
        "    raise SystemExit(1)\n"
        "print(f'p{pid}')\n",
        encoding="ascii",
    )
    probe.chmod(0o755)
    return probe


def run_sweep(
    *roots: Path, lsof: Path | None
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SWEEP)]
    for root in roots:
        command.extend(("--root", str(root)))
    if lsof is not None:
        command.extend(("--lsof", str(lsof)))
    return subprocess.run(command, text=True, capture_output=True, check=False)


def test_reaps_unheld_git_locks_but_leaves_non_git_locks(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    worktree_gitdir = repository / ".git" / "worktrees" / "worktree"
    worktree_gitdir.mkdir(parents=True)
    stale_index = repository / ".git" / "index.lock"
    stale_head = worktree_gitdir / "HEAD.lock"
    unrelated = repository / "runtime" / "unrelated.lock"
    unrelated.parent.mkdir()
    for path in (stale_index, stale_head, unrelated):
        path.touch()

    result = run_sweep(repository, lsof=lsof_probe(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "checked=2 reaped=2 held=0" in result.stdout
    assert not stale_index.exists()
    assert not stale_head.exists()
    assert unrelated.exists()


def test_worktree_gitdir_link_is_followed(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    gitdir = repository / ".git" / "worktrees" / "linked"
    gitdir.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="ascii")
    lock = gitdir / "index.lock"
    lock.touch()

    result = run_sweep(worktree, lsof=lsof_probe(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "checked=1 reaped=1" in result.stdout
    assert not lock.exists()


def test_live_holder_is_proven_and_lock_is_preserved(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    gitdir = repository / ".git"
    gitdir.mkdir(parents=True)
    lock = gitdir / "index.lock"
    holder_record = Path(str(lock) + ".holder-pid")
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys, time; "
                "stream = pathlib.Path(sys.argv[1]).open('a'); "
                "pathlib.Path(sys.argv[2]).write_text(str(__import__('os').getpid()), encoding='ascii'); "
                "time.sleep(20)"
            ),
            str(lock),
            str(holder_record),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if holder_record.exists():
                break
            time.sleep(0.01)
        assert holder_record.exists()
        lock.touch()

        result = run_sweep(repository, lsof=lsof_probe(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "checked=1 reaped=0 held=1" in result.stdout
        assert lock.exists()
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_missing_holder_probe_is_fail_closed(tmp_path: Path) -> None:
    repository = tmp_path / "repository" / ".git"
    repository.mkdir(parents=True)
    lock = repository / "index.lock"
    lock.touch()
    missing_lsof = tmp_path / "missing-lsof"

    result = run_sweep(repository.parent, lsof=missing_lsof)

    assert result.returncode == 0, result.stderr
    assert "checked=1 reaped=0 held=0 unavailable=1" in result.stdout
    assert lock.exists()


def test_holder_probe_diagnostic_is_not_mistaken_for_no_holder(tmp_path: Path) -> None:
    gitdir = tmp_path / "repository" / ".git"
    gitdir.mkdir(parents=True)
    lock = gitdir / "index.lock"
    lock.touch()
    probe = tmp_path / "failing-lsof"
    probe.write_text("#!/bin/sh\necho probe-failed >&2\nexit 1\n", encoding="ascii")
    probe.chmod(0o755)

    result = run_sweep(gitdir.parent, lsof=probe)

    assert result.returncode == 0, result.stderr
    assert "checked=1 reaped=0 held=0 unavailable=1" in result.stdout
    assert lock.exists()


def test_operator_sync_has_lock_sweep_before_checkout_sync() -> None:
    source = (ROOT / "ops" / "deployment_operator.sh").read_text(encoding="utf-8")
    sweep = source.index('git_lock_sweep "$n"')
    checkout = source.index('sync_to_run_branch "$PKGSRC"', sweep)
    assert sweep < checkout
