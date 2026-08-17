from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import pytest

from ops.revision_derivation import (
    RevisionDerivationError,
    assert_pair,
    build_is_current,
    engine_artifact_lock_path,
    publish_engine_product,
    reclaim_engine_artifacts,
    receipt_path,
    resolve_pair,
    write_build_receipt,
)


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "ops" / "revision_derivation.py"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "platform"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    engine_revision = "1" * 40
    pin = root / "control" / "engine-ref"
    pin.parent.mkdir()
    pin.write_text(engine_revision + "\n", encoding="ascii")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    return root, git(root, "rev-parse", "HEAD"), engine_revision


def test_resolution_reads_the_captured_commit_not_mutable_worktree(tmp_path: Path) -> None:
    root, platform_revision, engine_revision = repository(tmp_path)
    (root / "control" / "engine-ref").write_text("2" * 40 + "\n", encoding="ascii")

    pair = resolve_pair(root, "control/engine-ref")

    assert pair.platform_revision == platform_revision
    assert pair.engine_revision == engine_revision


def test_assert_pair_fails_when_platform_head_changes(tmp_path: Path) -> None:
    root, platform_revision, engine_revision = repository(tmp_path)
    git(root, "commit", "--allow-empty", "-qm", "advance")

    with pytest.raises(RevisionDerivationError, match="PLATFORM_REVISION_CHANGED"):
        assert_pair(root, "control/engine-ref", platform_revision, engine_revision)


@pytest.mark.parametrize("path", ["/absolute", "../escape", "control/../escape", ""])
def test_derivation_path_is_safe_relative(path: str, tmp_path: Path) -> None:
    root, _, _ = repository(tmp_path)
    with pytest.raises(RevisionDerivationError, match="DERIVATION_PATH_INVALID"):
        resolve_pair(root, path)


def test_revision_binary_path_and_receipt_attest_published_bytes(tmp_path: Path) -> None:
    source_revision = "a" * 40
    binary = tmp_path / "bin" / f"engine-{source_revision}"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    binary.chmod(0o755)
    command = ["cargo", "build", "-p", "engine"]
    write_build_receipt(binary, source_revision, command)

    assert binary.name == f"engine-{source_revision}"
    assert build_is_current(binary, source_revision, command)

    binary.write_text("#!/bin/sh\nexit 99\n", encoding="ascii")
    binary.chmod(0o755)
    assert not build_is_current(binary, source_revision, command)


def test_receipt_failure_leaves_only_an_unusable_revision_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ops.revision_derivation as derivation

    source_revision = "b" * 40
    product = tmp_path / "target" / "debug" / "engine"
    product.parent.mkdir(parents=True)
    product.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    product.chmod(0o755)
    binary = tmp_path / "bin" / f"engine-{source_revision}"
    command = ["cargo", "build", "-p", "engine"]

    def fail_receipt(*_args: object) -> None:
        raise OSError("injected receipt publication failure")

    monkeypatch.setattr(derivation, "write_build_receipt", fail_receipt)

    with pytest.raises(OSError, match="injected receipt publication failure"):
        publish_engine_product(product, binary, source_revision, command)

    assert binary.is_file()
    assert not binary.is_symlink()
    assert binary.read_bytes() == product.read_bytes()
    assert not receipt_path(binary).exists()
    assert not build_is_current(binary, source_revision, command)


def test_reclaimer_keeps_selected_and_live_engine_artifacts(tmp_path: Path) -> None:
    binary_base = tmp_path / "bin" / "engine"
    binary_base.parent.mkdir()
    selected = binary_base.with_name(f"engine-{'1' * 40}")
    live = binary_base.with_name(f"engine-{'2' * 40}")
    stale = binary_base.with_name(f"engine-{'3' * 40}")
    command = ["cargo", "build", "-p", "engine"]
    for binary in (selected, live, stale):
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        binary.chmod(0o755)
        write_build_receipt(binary, binary.name.rsplit("-", 1)[1], command)

    ready = tmp_path / "holder-ready"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,pathlib,sys,time; "
                "lock=pathlib.Path(sys.argv[1]).open('a+b'); "
                "fcntl.flock(lock,fcntl.LOCK_SH); pathlib.Path(sys.argv[2]).touch(); "
                "time.sleep(10)"
            ),
            str(engine_artifact_lock_path(live)),
            str(ready),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.01)
        assert ready.exists()

        assert not reclaim_engine_artifacts(binary_base, {selected})
        assert selected.is_file()
        assert live.is_file()
        assert receipt_path(live).is_file()
        assert not stale.exists()
        assert not receipt_path(stale).exists()
        assert not engine_artifact_lock_path(stale).exists()
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    assert reclaim_engine_artifacts(binary_base, {selected})
    assert selected.is_file()
    assert not live.exists()
    assert not receipt_path(live).exists()
    assert not engine_artifact_lock_path(live).exists()
