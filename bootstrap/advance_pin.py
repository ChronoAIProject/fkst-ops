#!/usr/bin/env python3
"""Advance one external-source pin from facts observed in its source repository."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bootstrap.canonical_tree import canonical_tree_sha256


_FULL_REVISION = re.compile(r"[0-9a-f]{40}")


def _run(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def _source_url(lock: dict[str, object], source_id: str) -> str:
    sources = lock.get("external_source")
    if not isinstance(sources, list):
        raise ValueError("lock.external_source must be an array of tables")
    matches = [entry for entry in sources if isinstance(entry, dict) and entry.get("id") == source_id]
    if len(matches) != 1:
        raise ValueError(f"lock must contain exactly one external_source(id={source_id})")
    url = matches[0].get("git")
    if not isinstance(url, str) or not url:
        raise ValueError(f"external_source(id={source_id}).git must be a non-empty string")
    parsed = urlsplit(url)
    if parsed.scheme in {"http", "https"} and (parsed.username is not None or parsed.password is not None):
        raise ValueError("credential-bearing HTTP(S) source URLs are forbidden")
    return url


def _updated_text(text: str, source_id: str, revision: str, tree: str) -> str:
    chunks = re.split(r"(?m)(?=^\[\[external_source\]\]\s*$)", text)
    changed = 0
    for index, chunk in enumerate(chunks):
        if not chunk.startswith("[[external_source]]"):
            continue
        parsed = tomllib.loads(chunk)
        entries = parsed.get("external_source", [])
        if len(entries) != 1 or entries[0].get("id") != source_id:
            continue
        chunk, rev_count = re.subn(
            r'(?m)^(rev\s*=\s*)"[^"]*"[ \t]*$', rf'\1"{revision}"', chunk
        )
        chunk, tree_count = re.subn(
            r'(?m)^(tree_sha256\s*=\s*)"[^"]*"[ \t]*$', rf'\1"{tree}"', chunk
        )
        if rev_count != 1 or tree_count != 1:
            raise ValueError(f"external_source(id={source_id}) needs one resolved rev and tree_sha256")
        chunks[index] = chunk
        changed += 1
    if changed != 1:
        raise ValueError(f"lock must contain exactly one external_source(id={source_id})")
    updated = "".join(chunks)
    tomllib.loads(updated)
    return updated


def advance(lock_path: Path, source_id: str, requested_revision: str) -> tuple[str, str]:
    text = lock_path.read_text(encoding="utf-8")
    lock = tomllib.loads(text)
    url = _source_url(lock, source_id)
    with tempfile.TemporaryDirectory(prefix="fkst-pin-") as temporary:
        checkout = Path(temporary) / "source"
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", url, str(checkout)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        revision = _run(checkout, "rev-parse", "--verify", requested_revision + "^{commit}")
        if not _FULL_REVISION.fullmatch(revision):
            raise ValueError("source resolved the requested revision to a non-canonical commit id")
        tree = canonical_tree_sha256(checkout, revision)
    updated = _updated_text(text, source_id, revision, tree)
    temporary_lock = lock_path.with_name(f".{lock_path.name}.{os.getpid()}.tmp")
    try:
        temporary_lock.write_text(updated, encoding="utf-8")
        os.replace(temporary_lock, lock_path)
    finally:
        temporary_lock.unlink(missing_ok=True)
    return revision, tree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    try:
        revision, tree = advance(args.lock, args.source, args.revision)
    except (OSError, ValueError, tomllib.TOMLDecodeError, subprocess.CalledProcessError) as exc:
        print(f"pin advance failed: {exc}", file=sys.stderr)
        return 2
    print(f"source={args.source}")
    print(f"revision={revision}")
    print(f"tree_sha256={tree}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
