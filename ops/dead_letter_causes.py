#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path


DEAD_LETTER_FACT = re.compile(
    r"(?:^|\s)dept=dead_letter\s+tag=DEAD_LETTER\s+"
    r"error_class=(?P<error_class>\S+)\s+"
    r"fingerprint=(?P<fingerprint>\S+).*?\s+"
    r"delivery_id=(?P<delivery_id>\S+)"
)


def archive_cause_facts(runtime_root: Path, output: Path) -> None:
    pattern = str(runtime_root / "logs" / "framework-child" / "*.log")
    retained: set[str] = set()
    for log_path_text in sorted(glob.glob(pattern)):
        log_path = Path(log_path_text)
        try:
            with log_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    match = DEAD_LETTER_FACT.search(line)
                    if match is None:
                        continue
                    retained.add(
                        "dept=dead_letter tag=DEAD_LETTER "
                        f"error_class={match.group('error_class')} "
                        f"fingerprint={match.group('fingerprint')} "
                        f"delivery_id={match.group('delivery_id')}\n"
                    )
        except OSError as exc:
            raise RuntimeError(f"could not archive structured fact log {log_path}: {exc}") from exc
    if not retained:
        return
    try:
        with output.open("a", encoding="utf-8") as handle:
            handle.writelines(sorted(retained))
    except OSError as exc:
        raise RuntimeError(f"could not write retained structured fact log {output}: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    archive_parser = commands.add_parser("archive")
    archive_parser.add_argument("--runtime-root", required=True, type=Path)
    archive_parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        archive_cause_facts(args.runtime_root, args.output)
    except (OSError, RuntimeError) as exc:
        print(f"dead-letter cause correlation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
