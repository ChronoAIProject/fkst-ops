from pathlib import Path
import subprocess

import pytest

from bootstrap.canonical_tree import canonical_tree_sha256
from watch.generate_artifacts import _verify_mechanism_root


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], text=True, capture_output=True, check=True
    ).stdout.strip()


def committed_source(root: Path) -> tuple[str, str]:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "mechanism.txt").write_text("pinned\n", encoding="ascii")
    git(root, "add", ".")
    git(root, "commit", "-qm", "pinned")
    revision = git(root, "rev-parse", "HEAD")
    return revision, canonical_tree_sha256(root, revision)


def mechanism_lock(path: Path, root: Path, revision: str, tree: str) -> Path:
    lock = path / "fkst.lock"
    lock.write_text(
        f'[[external_source]]\nid = "fkst-ops"\ngit = "{root}"\n'
        'checkout_role = "mechanism"\n'
        f'[external_source.resolved]\nrev = "{revision}"\ntree_sha256 = "{tree}"\n',
        encoding="ascii",
    )
    return lock


def test_pinned_clean_mechanism_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "mechanism"
    revision, tree = committed_source(root)

    _verify_mechanism_root(mechanism_lock(tmp_path, root, revision, tree), root)


def test_dirty_mechanism_root_names_the_dirt(tmp_path: Path) -> None:
    root = tmp_path / "mechanism"
    revision, tree = committed_source(root)
    lock = mechanism_lock(tmp_path, root, revision, tree)
    (root / "mechanism.txt").write_text("dirty\n", encoding="ascii")

    with pytest.raises(ValueError) as failure:
        _verify_mechanism_root(lock, root)

    assert revision in str(failure.value)
    assert "working tree dirty" in str(failure.value)


def test_wrong_revision_names_current_and_pinned_revisions(tmp_path: Path) -> None:
    root = tmp_path / "mechanism"
    pinned_revision, pinned_tree = committed_source(root)
    lock = mechanism_lock(tmp_path, root, pinned_revision, pinned_tree)
    (root / "mechanism.txt").write_text("next\n", encoding="ascii")
    git(root, "add", "mechanism.txt")
    git(root, "commit", "-qm", "next")
    current_revision = git(root, "rev-parse", "HEAD")

    with pytest.raises(ValueError) as failure:
        _verify_mechanism_root(lock, root)

    assert current_revision in str(failure.value)
    assert pinned_revision in str(failure.value)
    assert "working tree clean" in str(failure.value)
