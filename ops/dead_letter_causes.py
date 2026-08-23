#!/usr/bin/env python3
"""Render dominant causes for recent, exactly correlated dead-letter rows."""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


RECENT_WINDOW_MS = 6 * 60 * 60 * 1000
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


def recent_dead_letters(snapshot: dict[str, Any], now_ms: int) -> list[dict[str, Any]]:
    rows = snapshot.get("dead_letters")
    if not isinstance(rows, list):
        raise ValueError("observe snapshot dead_letters must be an array")
    recent = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("observe snapshot dead-letter rows must be objects")
        dead_at_ms = row.get("dead_at_ms", 0)
        if isinstance(dead_at_ms, bool) or not isinstance(dead_at_ms, (int, float)):
            raise ValueError("observe snapshot dead_at_ms must be numeric")
        if now_ms - dead_at_ms <= RECENT_WINDOW_MS:
            recent.append(row)
    return recent


def read_cause_facts(
    log_root: Path,
    run_name: str,
    delivery_ids: set[str],
) -> dict[str, set[tuple[str, str]]]:
    facts: dict[str, set[tuple[str, str]]] = defaultdict(set)
    runtime_pattern = str(
        log_root
        / f"dogfood-rt-{glob.escape(run_name)}.*"
        / "logs"
        / "framework-child"
        / "*.log"
    )
    log_paths = [Path(path) for path in glob.glob(runtime_pattern)]
    retained_log = log_root / f"{run_name}-dead-letter-facts.log"
    if retained_log.is_file():
        log_paths.append(retained_log)
    for log_path in sorted(log_paths):
        try:
            with log_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    match = DEAD_LETTER_FACT.search(line)
                    if match is None:
                        continue
                    delivery_id = match.group("delivery_id")
                    if delivery_id in delivery_ids:
                        facts[delivery_id].add(
                            (match.group("error_class"), match.group("fingerprint"))
                        )
        except OSError as exc:
            raise RuntimeError(f"could not read structured fact log {log_path}: {exc}") from exc
    return facts


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


def dept_text(depts: set[str]) -> str:
    ordered = sorted(depts)
    field = "dept" if len(ordered) == 1 else "depts"
    return f"{field}={','.join(ordered)}"


def render(snapshot: dict[str, Any], now_ms: int, log_root: Path, run_name: str) -> str:
    truncated = snapshot.get("truncated")
    if isinstance(truncated, dict) and truncated.get("dead_letters") is True:
        return "    dead-letter cause: unavailable (observe dead-letter details truncated)"

    recent = recent_dead_letters(snapshot, now_ms)
    if not recent:
        return ""

    delivery_ids = {
        str(row.get("delivery_id"))
        for row in recent
        if row.get("delivery_id") not in (None, "")
    }
    facts = read_cause_facts(log_root, run_name, delivery_ids)
    groups: dict[tuple[str, str] | None, dict[str, Any]] = {}
    for row in recent:
        delivery_id = str(row.get("delivery_id") or "")
        candidates = facts.get(delivery_id, set())
        cause = next(iter(candidates)) if len(candidates) == 1 else None
        group = groups.setdefault(cause, {"count": 0, "depts": set()})
        group["count"] += 1
        group["depts"].add(str(row.get("dept") or "unknown"))

    def label(cause: tuple[str, str] | None) -> str:
        if cause is None:
            return "unattributed"
        return f"error_class={cause[0]} fingerprint={cause[1]}"

    ranked = sorted(
        groups.items(),
        key=lambda item: (-int(item[1]["count"]), label(item[0])),
    )
    top_cause, top_group = ranked[0]
    line = (
        f"    dead-letter cause (top): {label(top_cause)} "
        f"{top_group['count']}x {dept_text(top_group['depts'])}"
    )
    unattributed = groups.get(None)
    if top_cause is not None and unattributed is not None:
        line += (
            f"; unattributed={unattributed['count']}x "
            f"{dept_text(unattributed['depts'])}"
        )
    return line


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render")
    render_parser.add_argument("--now-ms", required=True, type=int)
    render_parser.add_argument("--log-root", required=True, type=Path)
    render_parser.add_argument("--run-name", required=True)
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
        snapshot = json.load(sys.stdin)
        if not isinstance(snapshot, dict):
            raise ValueError("observe snapshot must be an object")
        output = render(snapshot, args.now_ms, args.log_root, args.run_name)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"dead-letter cause correlation failed: {exc}", file=sys.stderr)
        return 2
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
