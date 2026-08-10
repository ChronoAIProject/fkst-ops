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
import subprocess
import sys
from pathlib import Path


ENVIRONMENT_ARGUMENTS = {
    "deployment_repository": "FKST_WATCH_DEPLOYMENT_REPOSITORY",
    "machine_profile": "FKST_WATCH_MACHINE_PROFILE",
    "declaration_manifest": "FKST_WATCH_DECLARATION_MANIFEST",
    "ledger": "FKST_WATCH_LEDGER",
    "operator_entry": "FKST_WATCH_OPERATOR_ENTRY",
}


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


def invoke(
    entry: Path, repository: Path, declaration: Path, profile: Path, lock: Path, action: str
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "FKST_OPS_PYTHON": sys.executable}
    return subprocess.run(
        [
            str(entry),
            "--deployment-dir", str(repository),
            "--declaration",
            str(declaration),
            "--machine-profile",
            str(profile),
            "--lock", str(lock),
            action,
        ],
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
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not repository.is_dir() or not profile.is_file() or not manifest.is_file() or not entry.is_file():
        print("error: deployment repository, machine profile, declaration manifest, or operator entry does not exist", file=sys.stderr)
        return 2

    try:
        discovered, lock = declarations(repository, manifest)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = False
    for declaration in discovered:
        sync = invoke(entry, repository, declaration, profile, lock, "sync")
        status = invoke(entry, repository, declaration, profile, lock, "status")
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
