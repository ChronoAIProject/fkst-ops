#!/usr/bin/env python3
"""Reject concrete target names in the tracked fkst-ops source set."""

import argparse
import subprocess
import sys
from pathlib import Path

# Every exclusion is enumerated by exact path. Categories, directories, globs and implicit
# exclusions are forbidden: an unenumerated exclusion is a scan failure.
# Documents are excluded only when naming the origin repository is what makes their evidence
# verifiable — a doctrine or design anchor that cannot be traced back to its incident is worth
# nothing. Executable source is never excluded on that ground.
EXCLUDED_PATHS = frozenset({
    "CLAUDE.md",
    "README.md",
    "docs/superpowers/specs/2026-08-08-fkst-ops-extraction-design.md",
    "tests/schema/fixtures/fkst.lock",
    "tests/schema/fixtures/machine-profile.toml",
    "tests/schema/fixtures/packages.toml",
    "tests/schema/fixtures/substrate.toml",
    "tests/schema/fixtures/website.toml",
    "tests/scan/fixtures/concrete-name.txt",
    "tests/scan/test_zero_target_names.py",
})


def tracked_paths(root: Path) -> list[str]:
    # The input set is everything the next commit would carry: files already tracked, plus files
    # staged for addition. Scanning only tracked files makes a pre-commit run structurally
    # misleading — a newly written file is invisible until it is staged, so the gate reports green
    # and then turns red on the very commit that was just checked. Incident of record: a repository
    # guidance document and later a README each passed this scan before `git add` and failed it
    # after.
    paths: list[str] = []
    seen: set[str] = set()
    for args in (["ls-files", "-z"], ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"]):
        output = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
        for value in output.split(b"\0"):
            if not value:
                continue
            decoded = value.decode("utf-8", "surrogateescape")
            if decoded not in seen:
                seen.add(decoded)
                paths.append(decoded)
    return paths


def scan(root: Path, names: list[str], requested_exclusions: set[str]) -> list[str]:
    unknown = requested_exclusions - EXCLUDED_PATHS
    if unknown:
        raise ValueError("unenumerated exclusion(s): " + ", ".join(sorted(unknown)))
    findings: list[str] = []
    for relative in tracked_paths(root):
        if relative in EXCLUDED_PATHS:
            continue
        path = root / relative
        if not path.exists():
            continue
        data = path.read_bytes()
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
