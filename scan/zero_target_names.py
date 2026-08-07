#!/usr/bin/env python3
"""Reject concrete target names in the tracked fkst-ops source set."""

import argparse
import subprocess
import sys
from pathlib import Path

EXCLUDED_PATHS = frozenset({
    "docs/superpowers/specs/2026-08-08-fkst-ops-extraction-design.md",
    "tests/schema/fixtures/fkst.lock",
    "tests/schema/fixtures/machine-profile.toml",
    "tests/schema/fixtures/packages.toml",
    "tests/schema/fixtures/substrate.toml",
    "tests/schema/fixtures/website.toml",
    "tests/scan/fixtures/concrete-name.txt",
})


def tracked_paths(root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    return [value.decode("utf-8", "surrogateescape") for value in output.split(b"\0") if value]


def scan(root: Path, names: list[str], requested_exclusions: set[str]) -> list[str]:
    unknown = requested_exclusions - EXCLUDED_PATHS
    if unknown:
        raise ValueError("unenumerated exclusion(s): " + ", ".join(sorted(unknown)))
    findings: list[str] = []
    for relative in tracked_paths(root):
        if relative in EXCLUDED_PATHS:
            continue
        data = (root / relative).read_bytes()
        for name in names:
            if name.encode() in data:
                findings.append(f"{relative}: concrete target name: {name}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--name", action="append", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    try:
        findings = scan(args.root, args.name, set(args.exclude))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: zero-target-name scan failed: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
