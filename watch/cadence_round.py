#!/usr/bin/env python3
"""Run one externally scheduled sync round for a deployment repository.

The repository is an input control boundary and is deliberately never advanced here.
An operator adopts declaration or mechanism-pin changes by updating that checkout and
running artifact generation again.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path


ENVIRONMENT_ARGUMENTS = {
    "deployment_repository": "FKST_WATCH_DEPLOYMENT_REPOSITORY",
    "machine_profile": "FKST_WATCH_MACHINE_PROFILE",
    "declaration_manifest": "FKST_WATCH_DECLARATION_MANIFEST",
    "ledger": "FKST_WATCH_LEDGER",
    "operator_entry": "FKST_WATCH_OPERATOR_ENTRY",
}
STATUS_PATTERN = re.compile(r"^\[([^]]+)]\s+(\S+)(?:\s|$)")
RUNNING = "RUNNING"
STOPPED = "STOPPED"
UNKNOWN = "UNKNOWN"


def required_path(argument: str | None, environment_name: str, label: str) -> Path:
    value = argument if argument is not None else os.environ.get(environment_name)
    if not value:
        raise ValueError(f"{label} is required (argument or {environment_name})")
    return Path(value).expanduser().resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bound_file(repository: Path, record: object, label: str) -> Path:
    if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
        raise ValueError(f"manifest {label} binding is invalid")
    item, digest = record["path"], record["sha256"]
    if not isinstance(item, str) or not item or not isinstance(digest, str):
        raise ValueError(f"manifest {label} binding is invalid")
    relative = Path(item)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"forbidden {label} manifest path: {item}")
    path = (repository / relative).resolve()
    try:
        canonical_relative = path.relative_to(repository)
    except ValueError as exc:
        raise ValueError(f"{label} escapes repository: {item}") from exc
    if "tests" in canonical_relative.parts or ".fkst" in canonical_relative.parts:
        raise ValueError(f"forbidden canonical {label} target: {item} -> {path}")
    if not path.is_file():
        raise ValueError(f"enumerated {label} does not exist: {item}")
    if _sha256(path) != digest:
        raise ValueError(f"generated manifest is stale: {label} changed: {item}")
    return path


def declarations(repository: Path, manifest_path: Path) -> tuple[list[Path], Path]:
    repository_parts = repository.parts
    if "tests" in repository_parts or ".fkst" in repository_parts:
        raise ValueError(f"deployment repository is forbidden control material: {repository}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read declaration manifest: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "repository", "input_set", "lock", "declarations"}:
        raise ValueError("declaration manifest has unknown or missing fields")
    if manifest["schema"] != "fkst.ops.declaration-set.v2" or manifest["repository"] != str(repository):
        raise ValueError("declaration manifest schema or repository mismatch")
    _bound_file(repository, manifest["input_set"], "input set")
    relative_items = manifest["declarations"]
    if not isinstance(relative_items, list) or not relative_items:
        raise ValueError("declaration manifest must enumerate declarations")
    found: list[Path] = []
    for item in relative_items:
        found.append(_bound_file(repository, item, "declaration"))
    if len(set(found)) != len(found):
        raise ValueError("declaration manifest contains duplicates")
    lock = _bound_file(repository, manifest["lock"], "lock")
    return found, lock


def status_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def deployment_status(output: str, deployment_id: str) -> tuple[str, str]:
    matching: list[tuple[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        match = STATUS_PATTERN.match(line)
        if match is not None and match.group(1) == deployment_id:
            matching.append((match.group(2), line))
    if len(matching) != 1:
        return UNKNOWN, ""
    state, line = matching[0]
    return (state, line) if state in {RUNNING, STOPPED} else (UNKNOWN, line)


def deployment_ids(declaration: Path) -> list[str]:
    try:
        with declaration.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read deployment identities from {declaration}: {exc}") from exc
    deployments = document.get("deployment")
    if not isinstance(deployments, list) or not deployments:
        raise ValueError(f"declaration has no deployments: {declaration}")
    identities: list[str] = []
    for item in deployments:
        identity = item.get("id") if isinstance(item, dict) else None
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"declaration has an invalid deployment id: {declaration}")
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise ValueError(f"declaration has duplicate deployment ids: {declaration}")
    return identities


def ledger_records(ledger: Path) -> list[dict[str, object]]:
    if not ledger.exists():
        return []
    records: list[dict[str, object]] = []
    try:
        for number, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"ledger line {number} is not an object")
            records.append(record)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read guard state from ledger: {exc}") from exc
    return records


def prior_stopped_streak(
    records: list[dict[str, object]], declaration: str, deployment_id: str
) -> int:
    streak = 0
    for record in reversed(records):
        if record.get("deployment") != declaration:
            continue
        recorded_id = record.get("deployment_id")
        if recorded_id is not None and recorded_id != deployment_id:
            continue
        recorded_line = record.get("status_line")
        state = (
            deployment_status(recorded_line, deployment_id)[0]
            if isinstance(recorded_line, str)
            else UNKNOWN
        )
        if state != STOPPED:
            break
        streak += 1
    return streak


def invoke(
    entry: Path,
    repository: Path,
    declaration: Path,
    profile: Path,
    lock: Path,
    action: str,
    deployment_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "FKST_OPS_PYTHON": sys.executable}
    command = [
        str(entry),
        "--deployment-dir", str(repository),
        "--declaration", str(declaration),
        "--machine-profile", str(profile),
        "--lock", str(lock),
        action,
    ]
    if deployment_id is not None:
        command.append(deployment_id)
    return subprocess.run(
        command,
        env=environment,
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
    parser.add_argument("--declaration-manifest")
    parser.add_argument("--ledger")
    parser.add_argument("--guard-restart-attempt-limit", type=int, required=True)
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
        manifest_value = (
            args.declaration_manifest
            or os.environ.get(ENVIRONMENT_ARGUMENTS["declaration_manifest"])
            or str(Path.home() / ".fkst" / "machine" / "declarations.json")
        )
        manifest = Path(manifest_value).expanduser().resolve()
        ledger = required_path(args.ledger, ENVIRONMENT_ARGUMENTS["ledger"], "ledger")
        entry = required_path(args.operator_entry, ENVIRONMENT_ARGUMENTS["operator_entry"], "operator entry")
        if args.guard_restart_attempt_limit < 0:
            raise ValueError("guard restart attempt limit must be non-negative")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not repository.is_dir() or not profile.is_file() or not manifest.is_file() or not entry.is_file():
        print("error: deployment repository, machine profile, declaration manifest, or operator entry does not exist", file=sys.stderr)
        return 2

    try:
        discovered, lock = declarations(repository, manifest)
        history = ledger_records(ledger) if args.guard_restart_attempt_limit > 0 else []
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = False
    for declaration in discovered:
        # launchd's StartInterval does not stack: it will not begin a round while the previous one
        # is still running, so a slow round silently degrades the observed cadence to
        # `round duration + interval`. Nothing in the record distinguished that from launchd
        # skipping a firing, which left an observed drift - sixteen consecutive intervals of which
        # only two matched the declared 900 seconds, the longest being 62 minutes - unattributable.
        # These two durations are what separates the two explanations.
        sync_started = time.monotonic()
        sync = invoke(entry, repository, declaration, profile, lock, "sync")
        status_started = time.monotonic()
        status = invoke(entry, repository, declaration, profile, lock, "status")
        status_finished = time.monotonic()
        sync_ms = round((status_started - sync_started) * 1000)
        status_ms = round((status_finished - status_started) * 1000)
        relative_declaration = str(declaration.relative_to(repository))
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        if args.guard_restart_attempt_limit == 0:
            append_record(
                ledger,
                {
                    "timestamp": timestamp,
                    "deployment": relative_declaration,
                    "sync_exit_status": sync.returncode,
                    "sync_ms": sync_ms,
                    "status_ms": status_ms,
                    "status_line": status_line(status.stdout),
                },
            )
        else:
            try:
                identities = deployment_ids(declaration)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                failed = True
                identities = []
            for identity in identities:
                observed_state, observed_line = (
                    deployment_status(status.stdout, identity)
                    if sync.returncode == 0 and status.returncode == 0
                    else (UNKNOWN, "")
                )
                record: dict[str, object] = {
                    "timestamp": timestamp,
                    "deployment": relative_declaration,
                    "deployment_id": identity,
                    "sync_exit_status": sync.returncode,
                    "sync_ms": sync_ms,
                    "status_ms": status_ms,
                    "status_line": observed_line,
                }
                guard = None
                if observed_state == STOPPED:
                    prior_streak = prior_stopped_streak(
                        history, relative_declaration, identity
                    )
                    guard = (
                        "attempted"
                        if prior_streak < args.guard_restart_attempt_limit
                        else "open"
                    )
                    record["guard"] = guard
                append_record(ledger, record)
                history.append(record)
                if guard == "attempted":
                    restart = invoke(
                        entry,
                        repository,
                        declaration,
                        profile,
                        lock,
                        "restart",
                        identity,
                    )
                    if restart.returncode != 0:
                        failed = True
                    if restart.stderr:
                        sys.stderr.write(restart.stderr)
        if sync.returncode != 0 or status.returncode != 0:
            failed = True
        if sync.stderr:
            sys.stderr.write(sync.stderr)
        if status.stderr:
            sys.stderr.write(status.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
