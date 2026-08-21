#!/usr/bin/env python3
"""Invoke and render the implemented board provider planes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from invoke_provider import ContractViolation, VERSION, validate_envelope  # noqa: E402


def invoke(executable: str, contract: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    request = {"version": VERSION, "contract": contract, "input": payload}
    try:
        completed = subprocess.run(
            [executable], input=json.dumps(request), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        return None, str(exc)
    try:
        document = validate_envelope(completed.stdout, completed.returncode, contract)
    except ContractViolation as exc:
        return None, f"invalid provider output: {exc}"
    if document["ok"] is True:
        return document["result"], None
    failure = document.get("failure")
    return None, f"{failure['code']}: {failure['message']}"


def render_plane(name: str, result: dict[str, Any] | None, failure: str | None) -> None:
    print(f"[{name}]")
    if failure is not None:
        print(f"FAIL {name}: {failure}")
        return
    assert result is not None
    for row in result["rows"]:
        rendered = row["fields"].get("text")
        print(rendered if isinstance(rendered, str) else json.dumps(row, sort_keys=True))
    if name == "engine-durable":
        print(f"health: {result['health']['status']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-provider")
    parser.add_argument("--engine-provider", required=True)
    parser.add_argument("--github-input", type=Path)
    parser.add_argument("--engine-input", required=True, type=Path)
    args = parser.parse_args()
    try:
        engine_input = json.loads(args.engine_input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    engine = invoke(args.engine_provider, "fkst.ops.board.engine-durable.v1", engine_input)
    print("[github-control]")
    print("MISSING github-control: plane is not implemented")
    render_plane("engine-durable", *engine)
    return 1 if engine[1] is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
