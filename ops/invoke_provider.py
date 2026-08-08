#!/usr/bin/env python3
"""Invoke one direct fkst-ops provider and validate its common envelope."""

import json
import math
import os
import re
import subprocess
import sys
from typing import Any


VERSION = "fkst.ops.invocation.v1"
FAILURE_CODES = {
    "fkst.ops.engine.v1": {
        "INVALID_INPUT", "CHECKOUT_MISSING", "CONTRACT_MISSING", "WRONG_BRANCH", "UPDATE_FAILED", "BUILD_FAILED",
    },
    "fkst.ops.board.engine-durable.v1": {"INVALID_INPUT", "OBSERVE_FAILED", "CACHE_FAILED", "MALFORMED_FACT"},
    "fkst.ops.board.github-control.v1": {
        "INVALID_INPUT", "AUTH_FAILED", "FETCH_FAILED", "PRODUCER_FAILED", "MALFORMED_FACT",
    },
}


class ContractViolation(ValueError):
    pass


def _exact_members(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ContractViolation(f"{name} has missing or unknown members")


def _board_row(value: Any) -> None:
    if not isinstance(value, dict):
        raise ContractViolation("BoardRow must be an object")
    _exact_members(value, {"key", "classification", "fields"}, "BoardRow")
    if not isinstance(value["key"], str) or not isinstance(value["classification"], str):
        raise ContractViolation("BoardRow key and classification must be strings")
    if not isinstance(value["fields"], dict) or not all(isinstance(key, str) for key in value["fields"]):
        raise ContractViolation("BoardRow fields must be a string-keyed object")
    for field in value["fields"].values():
        if isinstance(field, (dict, list)) or not isinstance(field, (str, int, float, bool, type(None))):
            raise ContractViolation("BoardRow field values must be scalars")
        if isinstance(field, float) and not math.isfinite(field):
            raise ContractViolation("BoardRow numeric fields must be finite")


def _board_rows(value: Any) -> None:
    if not isinstance(value, list):
        raise ContractViolation("rows must be an array")
    for row in value:
        _board_row(row)


def _validate_result(contract: str, result: Any) -> None:
    if not isinstance(result, dict):
        raise ContractViolation("result must be an object")
    if contract == "fkst.ops.engine.v1":
        _exact_members(result, {"binary", "source_rev"}, "engine result")
        binary = result["binary"]
        if not isinstance(binary, str) or not os.path.isfile(binary) or not os.access(binary, os.X_OK):
            raise ContractViolation("engine result binary must be an existing executable")
        if not isinstance(result["source_rev"], str) or re.fullmatch(r"[0-9a-fA-F]{40}", result["source_rev"]) is None:
            raise ContractViolation("engine result source_rev must be a full Git SHA")
        return
    expected_view = "engine-durable" if contract == "fkst.ops.board.engine-durable.v1" else "github-control"
    expected_members = {"view", "rows", "health"} if expected_view == "engine-durable" else {"view", "rows"}
    _exact_members(result, expected_members, f"{expected_view} result")
    if result["view"] != expected_view:
        raise ContractViolation("board result has the wrong view")
    _board_rows(result["rows"])
    if expected_view == "engine-durable":
        health = result["health"]
        if not isinstance(health, dict):
            raise ContractViolation("BoardHealth must be an object")
        _exact_members(health, {"status", "anomalies"}, "BoardHealth")
        if not isinstance(health["status"], str):
            raise ContractViolation("BoardHealth status must be a string")
        _board_rows(health["anomalies"])


def validate_envelope(stdout: str, returncode: int, contract: str) -> dict[str, Any]:
    if contract not in FAILURE_CODES:
        raise ContractViolation("unknown selected contract")
    try:
        decoder = json.JSONDecoder()
        document, end = decoder.raw_decode(stdout)
        if stdout[end:].strip():
            raise ContractViolation("multiple JSON documents")
    except json.JSONDecodeError as exc:
        raise ContractViolation(f"malformed provider JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("version") != VERSION or not isinstance(document.get("ok"), bool):
        raise ContractViolation("provider output violates the invocation envelope")
    if document["ok"]:
        _exact_members(document, {"version", "ok", "result"}, "success envelope")
        if returncode != 0:
            raise ContractViolation("provider success object/exit mismatch")
        _validate_result(contract, document["result"])
    else:
        _exact_members(document, {"version", "ok", "failure"}, "failure envelope")
        if returncode not in (1, 2):
            raise ContractViolation("provider failure object/exit mismatch")
        failure = document["failure"]
        if not isinstance(failure, dict):
            raise ContractViolation("failure must be an object")
        _exact_members(failure, {"code", "message", "details"}, "failure")
        if failure["code"] not in FAILURE_CODES[contract] or not isinstance(failure["message"], str) or not isinstance(failure["details"], dict):
            raise ContractViolation("failure violates the selected contract")
    return document


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: invoke_provider.py EXECUTABLE CONTRACT", file=sys.stderr)
        return 2
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError) as exc:
        print(f"invalid provider input: {exc}", file=sys.stderr)
        return 2
    request = {"version": VERSION, "contract": sys.argv[2], "input": payload}
    try:
        run = subprocess.run([sys.argv[1]], input=json.dumps(request), text=True, capture_output=True)
    except OSError as exc:
        print(f"provider invocation failed: {exc}", file=sys.stderr)
        return 1
    if run.stderr:
        sys.stderr.write(run.stderr)
    try:
        result = validate_envelope(run.stdout, run.returncode, sys.argv[2])
    except ContractViolation as exc:
        print(f"invalid provider output: {exc}", file=sys.stderr)
        return 1
    if not result["ok"]:
        failure = result["failure"]
        print(
            f"provider failure [{sys.argv[2]}] {failure['code']}: {failure['message']}",
            file=sys.stderr,
        )
    json.dump(result, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if result["ok"] else run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
