#!/usr/bin/env python3
"""Pure helpers for deterministic fkst-framework BIN cache paths."""

from __future__ import annotations

from pathlib import Path
from typing import Final
import sys


_CACHE_PREFIX: Final = "fkst-substrate-bin"
_CACHE_VERSION: Final = "v1"
_BIN_RELATIVE_PATH: Final = ("target", "debug", "fkst-framework")
_SAFE_BYTES: Final = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
    b"0123456789"
    b"-_"
)


def encode_cache_component(value: str) -> str:
    """Return one filesystem-safe path component for an exact input value."""

    if not isinstance(value, str):
        raise TypeError("cache path component must be a string")
    if value == "":
        raise ValueError("cache path component must not be empty")
    if "\x00" in value:
        raise ValueError("cache path component must not contain NUL")

    encoded = value.encode("utf-8")
    return "".join(
        chr(byte) if byte in _SAFE_BYTES else f"%{byte:02X}"
        for byte in encoded
    )


def substrate_bin_cache_path(cache_root: str | Path, owner: str, repo: str, ref: str) -> Path:
    """Return the deterministic per-source-pin fkst-framework binary path.

    Path shape:
      <cache_root>/fkst-substrate-bin/v1/<owner>/<repo>/<ref>/target/debug/fkst-framework

    The owner, repo, and ref path components are independently percent-encoded
    UTF-8 components. Component boundaries are therefore structural separators,
    not data, so triples cannot collide through slash or separator replacement.
    """

    root = Path(cache_root)
    if str(root) == "":
        raise ValueError("cache root must not be empty")

    return root.joinpath(
        _CACHE_PREFIX,
        _CACHE_VERSION,
        encode_cache_component(owner),
        encode_cache_component(repo),
        encode_cache_component(ref),
        *_BIN_RELATIVE_PATH,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("usage: bin_cache.py <cache-root> <owner> <repo> <ref>", file=sys.stderr)
        return 2
    print(substrate_bin_cache_path(argv[1], argv[2], argv[3], argv[4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
