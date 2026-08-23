#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


OBSERVE_TIMEOUT_SECONDS = 1.0
DEAD_LETTER_FACT = re.compile(
    r"(?:^|\s)dept=dead_letter\s+tag=DEAD_LETTER\s+"
    r"error_class=(?P<error_class>\S+)\s+"
    r"fingerprint=(?P<fingerprint>\S+).*?\s+"
    r"delivery_id=(?P<delivery_id>\S+)"
)


def observe_snapshot(engine: Path, durable_root: Path) -> dict[str, Any]:
    command = [str(engine), "observe", "--durable-root", str(durable_root), "--json"]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start engine observe: {exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=OBSERVE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        raise RuntimeError(
            f"engine observe exceeded {OBSERVE_TIMEOUT_SECONDS:g}s deadline"
        ) from exc
    if process.returncode != 0:
        detail = stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"engine observe exited {process.returncode}{suffix}")
    try:
        snapshot = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"engine observe returned invalid JSON: {exc}") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("observe snapshot must be an object")
    return snapshot


def dead_letter_count(snapshot: dict[str, Any]) -> str:
    rows = snapshot.get("dead_letters")
    if not isinstance(rows, list):
        raise ValueError("observe snapshot dead_letters must be an array")
    truncated = snapshot.get("truncated")
    if not isinstance(truncated, dict) or not isinstance(
        truncated.get("dead_letters"), bool
    ):
        raise ValueError("observe snapshot truncated.dead_letters must be boolean")
    if truncated["dead_letters"]:
        return "unknown"
    return str(len(rows))


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
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--engine", required=True, type=Path)
    status_parser.add_argument("--durable-root", required=True, type=Path)
    archive_parser = commands.add_parser("archive")
    archive_parser.add_argument("--runtime-root", required=True, type=Path)
    archive_parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "archive":
            archive_cause_facts(args.runtime_root, args.output)
            return 0
        if args.command == "status":
            print(dead_letter_count(observe_snapshot(args.engine, args.durable_root)))
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"dead-letter cause correlation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
