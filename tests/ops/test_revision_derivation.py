from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from ops.revision_derivation import (
    RevisionDerivationError,
    assert_pair,
    build_is_current,
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


def test_shared_revision_cli_imports_schema_outside_repository(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "assert-shared",
            str(tmp_path / "missing-deployments"),
            str(tmp_path / "missing-profile.toml"),
            str(tmp_path / "missing-lock.toml"),
            str(tmp_path / "engine"),
        ],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert "DECLARATION_SET_INVALID" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_build_receipt_rejects_binary_outside_checkout_product(tmp_path: Path) -> None:
    root, source_revision, _ = repository(tmp_path)
    git(root, "checkout", "--detach", "-q")
    product = root / "target" / "debug" / "engine"
    product.parent.mkdir(parents=True)
    product.write_text("#!/bin/sh\n", encoding="ascii")
    product.chmod(0o755)
    binary = tmp_path / "bin" / "engine"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n", encoding="ascii")
    binary.chmod(0o755)
    command = ["cargo", "build", "-p", "engine"]
    write_build_receipt(binary, source_revision, command)

    assert not build_is_current(binary, root, source_revision, command)
