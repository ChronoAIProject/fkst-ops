#!/usr/bin/env python3
"""Compute the fkst-ops lock contract's canonical tracked-tree hash."""

import hashlib
import struct
import subprocess
import sys
from pathlib import Path


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def canonical_tree_sha256(root: Path, revision: str) -> str:
    entries: list[tuple[bytes, bytes, bytes]] = []
    output = git(root, "ls-tree", "-r", "-z", "--full-tree", revision)
    for raw in output.split(b"\0"):
        if not raw:
            continue
        metadata, path = raw.split(b"\t", 1)
        mode, object_type, object_id = metadata.split(b" ", 2)
        if object_type != b"blob":
            raise RuntimeError(f"tracked path {path!r} is not a blob")
        entries.append((path, mode, object_id))

    digest = hashlib.sha256()
    for path, mode, object_id in sorted(entries, key=lambda entry: entry[0]):
        blob = git(root, "cat-file", "blob", object_id.decode("ascii"))
        for field in (path, mode, hashlib.sha256(blob).digest()):
            digest.update(struct.pack(">Q", len(field)))
            digest.update(field)
    return "sha256-" + digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: canonical_tree.py <git-root> <revision>", file=sys.stderr)
        return 2
    try:
        print(canonical_tree_sha256(Path(sys.argv[1]), sys.argv[2]))
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"error: cannot compute canonical tree hash: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
