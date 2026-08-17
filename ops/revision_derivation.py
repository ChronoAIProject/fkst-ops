#!/usr/bin/env python3
"""Resolve and reassert a revision declared by a file in a checkout commit."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REVISION = re.compile(r"^[0-9a-f]{40}$")
RECEIPT_SCHEMA = "fkst.ops.engine-build.v1"


class RevisionDerivationError(ValueError):
    """A narrow failure at the declared revision boundary."""


@dataclass(frozen=True)
class RevisionPair:
    platform_revision: str
    engine_revision: str


def validate_derivation_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or "\\" in value
        or any(character.isspace() or character == "\x00" for character in value)
    ):
        raise RevisionDerivationError(
            f"DERIVATION_PATH_INVALID: expected a safe relative file path, got {value!r}"
        )
    return value


def _git(checkout: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RevisionDerivationError(
            f"PLATFORM_CHECKOUT_UNREADABLE: cannot run git in {checkout}: {exc}"
        ) from exc
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", "replace").strip()
        raise RevisionDerivationError(
            f"PLATFORM_CHECKOUT_UNREADABLE: git {' '.join(arguments)} failed in "
            f"{checkout}: {diagnostic or f'exit {result.returncode}'}"
        )
    return result.stdout


def resolve_pair(checkout: Path, relative_path: str) -> RevisionPair:
    """Capture checkout HEAD, then read the declared revision from that commit."""
    validate_derivation_path(relative_path)
    platform_revision = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}").decode(
        "ascii", "strict"
    ).strip()
    if REVISION.fullmatch(platform_revision) is None:
        raise RevisionDerivationError(
            f"PLATFORM_REVISION_INVALID: checkout HEAD is not a full lowercase Git SHA: "
            f"{platform_revision!r}"
        )
    try:
        raw_revision = _git(checkout, "show", f"{platform_revision}:{relative_path}").decode(
            "ascii", "strict"
        )
    except UnicodeError as exc:
        raise RevisionDerivationError(
            f"DERIVED_REVISION_INVALID: {relative_path} at {platform_revision} is not ASCII"
        ) from exc
    engine_revision = raw_revision.removesuffix("\n")
    if REVISION.fullmatch(engine_revision) is None:
        raise RevisionDerivationError(
            f"DERIVED_REVISION_INVALID: {relative_path} at {platform_revision} must contain "
            "exactly one full lowercase Git SHA"
        )
    return RevisionPair(platform_revision, engine_revision)


def assert_pair(
    checkout: Path,
    relative_path: str,
    expected_platform_revision: str,
    expected_engine_revision: str,
) -> None:
    observed = resolve_pair(checkout, relative_path)
    if observed.platform_revision != expected_platform_revision:
        raise RevisionDerivationError(
            "PLATFORM_REVISION_CHANGED: expected platform revision "
            f"{expected_platform_revision}, found {observed.platform_revision}"
        )
    if observed.engine_revision != expected_engine_revision:
        raise RevisionDerivationError(
            "DERIVED_REVISION_CHANGED: expected derived revision "
            f"{expected_engine_revision}, found {observed.engine_revision}"
        )


def receipt_path(binary: Path) -> Path:
    return binary.with_name(f".{binary.name}.build-receipt.json")


def write_build_receipt(binary: Path, source_revision: str, build_command: list[str]) -> None:
    document = {
        "schema": RECEIPT_SCHEMA,
        "source_revision": source_revision,
        "build_command": build_command,
    }
    destination = receipt_path(binary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    os.replace(temporary, destination)


def engine_product(engine_checkout: Path, build_command: list[str]) -> Path:
    """Return the one executable path reproducible from the declared Cargo command."""
    if build_command and Path(build_command[0]).name == "cargo" and "-p" in build_command:
        package_index = build_command.index("-p") + 1
        if package_index < len(build_command):
            package = build_command[package_index]
            if package and Path(package).name == package:
                return engine_checkout / "target" / "debug" / package
    raise RevisionDerivationError(
        "ENGINE_PRODUCT_UNRESOLVABLE: build command must select one Cargo package with -p"
    )


def build_is_current(
    binary: Path, engine_checkout: Path, source_revision: str, build_command: list[str]
) -> bool:
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return False
    try:
        document = json.loads(receipt_path(binary).read_text(encoding="ascii"))
        head = _git(engine_checkout, "rev-parse", "--verify", "HEAD^{commit}").decode(
            "ascii", "strict"
        ).strip()
        branch = _git(engine_checkout, "branch", "--show-current").decode("ascii", "strict").strip()
        product = engine_product(engine_checkout, build_command)
        binary_target = binary.resolve(strict=True)
        product_target = product.resolve(strict=True)
    except (OSError, UnicodeError, json.JSONDecodeError, RevisionDerivationError):
        return False
    return (
        document
        == {
            "schema": RECEIPT_SCHEMA,
            "source_revision": source_revision,
            "build_command": build_command,
        }
        and head == source_revision
        and branch == ""
        and product.is_file()
        and os.access(product, os.X_OK)
        and binary_target == product_target
    )


def _declaration_paths(repository: Path) -> list[Path]:
    manifest = repository / "deployment-set.json"
    try:
        document = json.loads(manifest.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RevisionDerivationError(
            f"DECLARATION_SET_INVALID: cannot read {manifest}: {exc}"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "declarations"}
        or document.get("schema") != "fkst.ops.declaration-input.v1"
        or not isinstance(document.get("declarations"), list)
        or not document["declarations"]
    ):
        raise RevisionDerivationError("DECLARATION_SET_INVALID: closed declaration set mismatch")
    paths: list[Path] = []
    for value in document["declarations"]:
        if not isinstance(value, str) or not value:
            raise RevisionDerivationError("DECLARATION_SET_INVALID: declaration path is invalid")
        relative = Path(value)
        candidate = (repository / relative).resolve()
        try:
            candidate.relative_to(repository.resolve())
        except ValueError as exc:
            raise RevisionDerivationError(
                f"DECLARATION_SET_INVALID: declaration escapes repository: {value}"
            ) from exc
        if relative.is_absolute() or ".." in relative.parts or not candidate.is_file():
            raise RevisionDerivationError(
                f"DECLARATION_SET_INVALID: declaration path is unsafe or missing: {value}"
            )
        paths.append(candidate)
    if len(set(paths)) != len(paths):
        raise RevisionDerivationError("DECLARATION_SET_INVALID: duplicate declaration path")
    return paths


def assert_shared_binary_revision(
    repository: Path, profile: Path, lock: Path, binary: Path
) -> None:
    """Reject differing derived revisions for declarations sharing one binary."""
    from schema.validator import ValidationError, load_and_resolve

    observations: list[tuple[str, RevisionPair]] = []
    try:
        for declaration in _declaration_paths(repository):
            resolved = load_and_resolve(declaration, profile, lock)
            for deployment in resolved["deployment"]:
                machine = deployment["machine"]
                if Path(machine["engine_binary"]).resolve() != binary.resolve():
                    continue
                derivation = deployment["engine_revision"]
                checkout = Path(machine["platform_checkout"])
                observations.append(
                    (deployment["id"], resolve_pair(checkout, derivation["path"]))
                )
    except ValidationError as exc:
        raise RevisionDerivationError(f"DECLARATION_SET_INVALID: {exc}") from exc
    revisions = {pair.engine_revision for _, pair in observations}
    if not observations:
        raise RevisionDerivationError(
            f"SHARED_ENGINE_BINARY_UNDECLARED: no declaration owns binary {binary}"
        )
    if len(revisions) > 1:
        details = ", ".join(
            f"{identity}={pair.engine_revision}@{pair.platform_revision}"
            for identity, pair in observations
        )
        raise RevisionDerivationError(
            f"SHARED_ENGINE_REVISION_CONFLICT: shared binary {binary} has differing declarations: {details}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("checkout", type=Path)
    resolve.add_argument("path")
    assertion = subparsers.add_parser("assert")
    assertion.add_argument("checkout", type=Path)
    assertion.add_argument("path")
    assertion.add_argument("platform_revision")
    assertion.add_argument("engine_revision")
    current = subparsers.add_parser("receipt-current")
    current.add_argument("binary", type=Path)
    current.add_argument("engine_checkout", type=Path)
    current.add_argument("source_revision")
    current.add_argument("build_command_json")
    shared = subparsers.add_parser("assert-shared")
    shared.add_argument("repository", type=Path)
    shared.add_argument("profile", type=Path)
    shared.add_argument("lock", type=Path)
    shared.add_argument("binary", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve":
            pair = resolve_pair(args.checkout, args.path)
            print(pair.platform_revision, pair.engine_revision, sep="\t")
        elif args.command == "assert":
            assert_pair(
                args.checkout, args.path, args.platform_revision, args.engine_revision
            )
        elif args.command == "receipt-current":
            command = json.loads(args.build_command_json)
            if isinstance(command, dict):
                command = command.get("build_command")
            if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
                raise RevisionDerivationError("BUILD_RECEIPT_INPUT_INVALID: build command is invalid")
            if not build_is_current(
                args.binary, args.engine_checkout, args.source_revision, command
            ):
                return 1
        else:
            assert_shared_binary_revision(
                args.repository, args.profile, args.lock, args.binary
            )
    except (OSError, UnicodeError, json.JSONDecodeError, RevisionDerivationError) as exc:
        print(f"engine revision resolution failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
