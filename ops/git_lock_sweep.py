#!/usr/bin/env python3
"""Reap unheld Git lock files beneath declared deployment roots."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess


@dataclass(frozen=True)
class SweepResult:
    checked: int = 0
    reaped: int = 0
    held: int = 0
    unavailable: int = 0
    errors: int = 0


def _git_lock(path: Path, linked_gitdir: bool = False) -> bool:
    """Accept only *.lock files in a .git tree or linked Git directory."""
    return path.name.endswith(".lock") and (linked_gitdir or ".git" in path.parts)


def _linked_gitdir(marker: Path) -> Path | None:
    try:
        content = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not content.startswith("gitdir:"):
        return None
    value = content[len("gitdir:"):].strip()
    if not value:
        return None
    target = Path(value)
    return (marker.parent / target).resolve() if not target.is_absolute() else target.resolve()


def candidates(roots: tuple[Path, ...]) -> list[Path]:
    found: set[Path] = set()
    pending = [(root, False) for root in dict.fromkeys(roots)]
    scanned: set[tuple[Path, bool]] = set()
    while pending:
        root, linked_gitdir = pending.pop()
        if not root.is_dir():
            continue
        scan_key = (root, linked_gitdir)
        if scan_key in scanned:
            continue
        scanned.add(scan_key)
        for directory, _, names in os.walk(root, followlinks=False):
            for name in names:
                path = Path(directory) / name
                if name == ".git":
                    linked = _linked_gitdir(path)
                    if linked is not None:
                        pending.append((linked, True))
                if not _git_lock(path, linked_gitdir) or path in found:
                    continue
                try:
                    mode = path.stat(follow_symlinks=False).st_mode
                except OSError:
                    continue
                if stat.S_ISREG(mode):
                    found.add(path)
    return sorted(found)


def _holder_state(lsof: str | None, path: Path) -> str:
    """Return held, unheld, or unknown from the carried lsof executable."""
    if not lsof:
        return "unknown"
    try:
        result = subprocess.run(
            [lsof, "-F", "p", "--", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    if (
        result.returncode == 1
        and not result.stdout.strip()
        and not result.stderr.strip()
    ):
        return "unheld"
    if result.returncode != 0:
        return "unknown"
    holders = [
        line[1:]
        for line in result.stdout.splitlines()
        if line.startswith("p") and line[1:].isdigit()
    ]
    return "held" if holders else "unknown"


def sweep(roots: tuple[Path, ...], lsof: str | None) -> SweepResult:
    checked = reaped = held = unavailable = errors = 0
    for path in candidates(roots):
        checked += 1
        state = _holder_state(lsof, path)
        if state == "held":
            held += 1
            continue
        if state == "unknown":
            unavailable += 1
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            errors += 1
            continue
        reaped += 1
    return SweepResult(checked, reaped, held, unavailable, errors)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--lsof")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    result = sweep(tuple(path.expanduser().resolve() for path in args.root), args.lsof)
    print(
        "git-lock-sweep"
        f" checked={result.checked} reaped={result.reaped}"
        f" held={result.held} unavailable={result.unavailable} errors={result.errors}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
