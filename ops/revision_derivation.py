#!/usr/bin/env python3
"""Resolve and reassert a revision declared by a file in a checkout commit."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


REVISION = re.compile(r"^[0-9a-f]{40}$")
RECEIPT_SCHEMA = "fkst.ops.engine-build.v2"


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


def binary_sha256(binary: Path) -> str:
    digest = hashlib.sha256()
    with binary.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256-" + digest.hexdigest()


def lock_engine_checkout(checkout: Path):
    path = checkout.parent / f".{checkout.name}.build.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    fcntl.flock(stream, fcntl.LOCK_EX)
    return stream


def write_build_receipt(binary: Path, source_revision: str, build_command: list[str]) -> None:
    document = {
        "schema": RECEIPT_SCHEMA,
        "source_revision": source_revision,
        "build_command": build_command,
        "binary_sha256": binary_sha256(binary),
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
    binary: Path, source_revision: str, build_command: list[str]
) -> bool:
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        return False
    try:
        document = json.loads(receipt_path(binary).read_text(encoding="ascii"))
        digest = binary_sha256(binary)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return document == {
        "schema": RECEIPT_SCHEMA,
        "source_revision": source_revision,
        "build_command": build_command,
        "binary_sha256": digest,
    }


def publish_engine_product(
    product: Path, binary: Path, source_revision: str, build_command: list[str]
) -> None:
    if not binary.name.endswith(f"-{source_revision}"):
        raise RevisionDerivationError(
            f"ENGINE_BINARY_NOT_REVISION_ADDRESSED: {binary} does not name {source_revision}"
        )
    binary.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{binary.name}.", suffix=".tmp", dir=binary.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(product, temporary)
        temporary.chmod(product.stat().st_mode & 0o777)
        try:
            os.link(temporary, binary)
        except FileExistsError:
            if binary.is_symlink() or binary_sha256(binary) != binary_sha256(temporary):
                raise RevisionDerivationError(
                    f"ENGINE_ARTIFACT_CONFLICT: refusing to replace existing {binary}"
                )
        write_build_receipt(binary, source_revision, build_command)
    finally:
        temporary.unlink(missing_ok=True)


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
    current.add_argument("source_revision")
    current.add_argument("build_command_json")
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
            if not build_is_current(args.binary, args.source_revision, command):
                return 1
    except (OSError, UnicodeError, json.JSONDecodeError, RevisionDerivationError) as exc:
        print(f"engine revision resolution failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
