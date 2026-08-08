#!/usr/bin/env python3
"""Update an engine checkout and execute its declared build command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


VERSION = "fkst.ops.invocation.v1"
CONTRACT = "fkst.ops.engine.v1"
INPUT_FIELDS = {"engine_checkout", "engine_binary", "expected_branch", "operation", "build_command"}
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
            ("engine_checkout", "engine_binary", "expected_branch", "operation"))
        or value["operation"] != "build"
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
    try:
        branch = run_git(checkout, "branch", "--show-current")
    except OSError as exc:
        return fail("CONTRACT_MISSING", f"cannot inspect engine checkout: {exc}", 2)
    if branch.returncode != 0:
        return fail("CONTRACT_MISSING", f"cannot inspect engine branch: {diagnostic(branch)}", 2)
    if branch.stdout.strip() != value["expected_branch"]:
        return fail("WRONG_BRANCH", f"expected branch {value['expected_branch']}, found {branch.stdout.strip()}", 1)

    update = run_git(checkout, "pull", "--ff-only")
    if update.returncode != 0:
        return fail("UPDATE_FAILED", diagnostic(update), 1)
    try:
        build = subprocess.run(
            value["build_command"], cwd=checkout, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except OSError as exc:
        return fail("BUILD_FAILED", str(exc), 1)
    if build.returncode != 0:
        return fail("BUILD_FAILED", diagnostic(build), 1)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return fail("CONTRACT_MISSING", f"engine binary is missing or not executable: {binary}", 2)
    revision = run_git(checkout, "rev-parse", "HEAD")
    if revision.returncode != 0:
        return fail("CONTRACT_MISSING", f"cannot resolve engine source revision: {diagnostic(revision)}", 2)
    emit({"version": VERSION, "ok": True, "result": {"binary": str(binary), "source_rev": revision.stdout.strip()}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
