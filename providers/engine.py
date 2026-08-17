#!/usr/bin/env python3
"""Update an engine checkout and execute its declared build command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.revision_derivation import RevisionDerivationError, engine_product, write_build_receipt


VERSION = "fkst.ops.invocation.v1"
CONTRACT = "fkst.ops.engine.v1"
INPUT_FIELDS = {"engine_checkout", "engine_binary", "expected_revision", "operation", "build_command"}
# engine-contract-input: the closed, generic five-field engine invocation contract.


def emit(document: dict[str, Any]) -> None:
    print(json.dumps(document, sort_keys=True, separators=(",", ":")))


def fail(code: str, message: str, exit_code: int) -> int:
    emit({"version": VERSION, "ok": False, "failure": {"code": code, "message": message, "details": {}}})
    return exit_code


def run_git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=checkout, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )


def diagnostic(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return fail("INVALID_INPUT", f"invalid invocation JSON: {exc}", 2)
    if (
        not isinstance(request, dict)
        or set(request) != {"version", "contract", "input"}
        or request.get("version") != VERSION
        or request.get("contract") != CONTRACT
    ):
        return fail("INVALID_INPUT", "invocation version, contract, or fields mismatch", 2)
    value = request.get("input")
    if not isinstance(value, dict) or set(value) != INPUT_FIELDS:
        return fail("INVALID_INPUT", "input must contain exactly the engine fields", 2)
    if (
        any(not isinstance(value[field], str) or not value[field] for field in
            ("engine_checkout", "engine_binary", "expected_revision", "operation"))
        or value["operation"] != "build"
        or re.fullmatch(r"[0-9a-f]{40}", value["expected_revision"]) is None
        or not isinstance(value["build_command"], list)
        or not value["build_command"]
        or any(not isinstance(arg, str) or not arg for arg in value["build_command"])
    ):
        return fail("INVALID_INPUT", "engine fields must have their declared types and operation must be build", 2)

    checkout = Path(value["engine_checkout"])
    binary = Path(value["engine_binary"])
    if not checkout.is_dir():
        return fail("CHECKOUT_MISSING", f"engine checkout does not exist: {checkout}", 1)
    if not (checkout / ".git").exists():
        return fail("CONTRACT_MISSING", f"engine checkout has no .git contract: {checkout}", 2)
    expected_revision = value["expected_revision"]
    try:
        update = run_git(checkout, "fetch", "--no-tags", "origin", expected_revision)
        if update.returncode == 0:
            update = run_git(checkout, "checkout", "--detach", expected_revision)
    except OSError as exc:
        return fail("UPDATE_FAILED", f"cannot fetch or check out exact engine revision: {exc}", 1)
    if update.returncode != 0:
        return fail("UPDATE_FAILED", diagnostic(update), 1)
    revision = run_git(checkout, "rev-parse", "--verify", "HEAD^{commit}")
    if revision.returncode != 0:
        return fail("CONTRACT_MISSING", f"cannot resolve engine source revision: {diagnostic(revision)}", 2)
    if revision.stdout.strip() != expected_revision:
        return fail(
            "REVISION_MISMATCH",
            f"expected engine revision {expected_revision}, found {revision.stdout.strip()}",
            1,
        )
    try:
        product = engine_product(checkout, value["build_command"])
    except RevisionDerivationError as exc:
        return fail("INVALID_INPUT", str(exc), 2)
    try:
        build = subprocess.run(
            value["build_command"], cwd=checkout, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except OSError as exc:
        return fail("BUILD_FAILED", str(exc), 1)
    if build.returncode != 0:
        return fail("BUILD_FAILED", diagnostic(build), 1)
    if not product.is_file() or not os.access(product, os.X_OK):
        return fail("CONTRACT_MISSING", f"engine build did not produce executable: {product}", 2)
    revision = run_git(checkout, "rev-parse", "--verify", "HEAD^{commit}")
    if revision.returncode != 0:
        return fail("CONTRACT_MISSING", f"cannot resolve engine source revision: {diagnostic(revision)}", 2)
    source_rev = revision.stdout.strip()
    if source_rev != expected_revision:
        return fail(
            "REVISION_MISMATCH",
            f"engine build changed source revision: expected {expected_revision}, found {source_rev}",
            1,
        )
    try:
        binary.parent.mkdir(parents=True, exist_ok=True)
        if binary.resolve(strict=False) != product.resolve(strict=True):
            pointer = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
            pointer.unlink(missing_ok=True)
            pointer.symlink_to(product)
            os.replace(pointer, binary)
        if binary.resolve(strict=True) != product.resolve(strict=True):
            return fail(
                "BINARY_PROVENANCE_MISMATCH",
                "engine binary does not resolve to the detached checkout product",
                2,
            )
        write_build_receipt(binary, source_rev, value["build_command"])
    except OSError as exc:
        return fail("CONTRACT_MISSING", f"cannot publish engine build receipt: {exc}", 2)
    emit({"version": VERSION, "ok": True, "result": {"binary": str(binary), "source_rev": source_rev}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
