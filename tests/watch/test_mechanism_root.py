from pathlib import Path
import subprocess

import pytest

from watch.generate_artifacts import _verify_mechanism_root


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], text=True, capture_output=True, check=True
    ).stdout.strip()


def committed_source(root: Path) -> str:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "mechanism.txt").write_text("pinned\n", encoding="ascii")
    git(root, "add", ".")
    git(root, "commit", "-qm", "pinned")
    return git(root, "rev-parse", "HEAD")


def mechanism_lock(path: Path, root: Path, revision: str) -> Path:
    lock = path / "fkst.lock"
    lock.write_text(
        f'[[external_source]]\nid = "fkst-ops"\ngit = "{root}"\n'
        'checkout_role = "mechanism"\n'
        f'[external_source.resolved]\nrev = "{revision}"\n',
        encoding="ascii",
    )
    return lock


def test_pinned_mechanism_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "mechanism"
    revision = committed_source(root)

    _verify_mechanism_root(mechanism_lock(tmp_path, root, revision), root)


def test_modified_tracked_mechanism_root_at_pinned_revision_is_accepted(
    tmp_path: Path,
) -> None:
    root = tmp_path / "mechanism"
    revision = committed_source(root)
    lock = mechanism_lock(tmp_path, root, revision)
    (root / "mechanism.txt").write_text("operator edit\n", encoding="ascii")

    _verify_mechanism_root(lock, root)


def test_wrong_revision_names_current_and_pinned_revisions(tmp_path: Path) -> None:
    root = tmp_path / "mechanism"
    pinned_revision = committed_source(root)
    lock = mechanism_lock(tmp_path, root, pinned_revision)
    (root / "mechanism.txt").write_text("next\n", encoding="ascii")
    git(root, "add", "mechanism.txt")
    git(root, "commit", "-qm", "next")
    current_revision = git(root, "rev-parse", "HEAD")

    with pytest.raises(ValueError) as failure:
        _verify_mechanism_root(lock, root)

    assert current_revision in str(failure.value)
    assert pinned_revision in str(failure.value)
