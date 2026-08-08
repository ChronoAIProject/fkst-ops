#!/usr/bin/env python3
"""Run one externally scheduled sync round for a deployment repository."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


ENVIRONMENT_ARGUMENTS = {
    "deployment_repository": "FKST_WATCH_DEPLOYMENT_REPOSITORY",
    "machine_profile": "FKST_WATCH_MACHINE_PROFILE",
    "ledger": "FKST_WATCH_LEDGER",
    "operator_entry": "FKST_WATCH_OPERATOR_ENTRY",
}


def required_path(argument: str | None, environment_name: str, label: str) -> Path:
    value = argument if argument is not None else os.environ.get(environment_name)
    if not value:
        raise ValueError(f"{label} is required (argument or {environment_name})")
    return Path(value).expanduser().resolve()


def declarations(repository: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(repository.rglob("*.toml")):
        if not path.is_file():
            continue
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        if document.get("schema") == "fkst.ops.deployment.v1":
            found.append(path)
    return found


def status_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def invoke(entry: Path, declaration: Path, profile: Path, action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(entry),
            "--declaration",
            str(declaration),
            "--machine-profile",
            str(profile),
            action,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def append_record(ledger: Path, record: dict[str, object]) -> None:
    encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ledger, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-repository")
    parser.add_argument("--machine-profile")
    parser.add_argument("--ledger")
    parser.add_argument("--operator-entry")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    try:
        repository = required_path(
            args.deployment_repository,
            ENVIRONMENT_ARGUMENTS["deployment_repository"],
            "deployment repository",
        )
        profile = required_path(args.machine_profile, ENVIRONMENT_ARGUMENTS["machine_profile"], "machine profile")
        ledger = required_path(args.ledger, ENVIRONMENT_ARGUMENTS["ledger"], "ledger")
        entry = required_path(args.operator_entry, ENVIRONMENT_ARGUMENTS["operator_entry"], "operator entry")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not repository.is_dir() or not profile.is_file() or not entry.is_file():
        print("error: deployment repository, machine profile, or operator entry does not exist", file=sys.stderr)
        return 2

    discovered = declarations(repository)
    if not discovered:
        print(f"error: no deployment declarations found in {repository}", file=sys.stderr)
        return 2

    failed = False
    for declaration in discovered:
        sync = invoke(entry, declaration, profile, "sync")
        status = invoke(entry, declaration, profile, "status")
        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "deployment": str(declaration.relative_to(repository)),
            "sync_exit_status": sync.returncode,
            "status_line": status_line(status.stdout),
        }
        append_record(ledger, record)
        if sync.returncode != 0 or status.returncode != 0:
            failed = True
        if sync.stderr:
            sys.stderr.write(sync.stderr)
        if status.stderr:
            sys.stderr.write(status.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
