#!/usr/bin/env python3
"""Invoke and render the two required board provider planes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

VERSION = "fkst.ops.invocation.v1"
def invoke(executable: str, contract: str, view: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    request = {"version": VERSION, "contract": contract, "input": payload}
    try:
        completed = subprocess.run(
            [executable], input=json.dumps(request), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        return None, str(exc)
    try:
        decoder = json.JSONDecoder()
        document, end = decoder.raw_decode(completed.stdout)
        if completed.stdout[end:].strip():
            raise ValueError("multiple JSON documents")
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"invalid provider output: {exc}"
    if not isinstance(document, dict) or document.get("version") != VERSION or not isinstance(document.get("ok"), bool):
        return None, "provider output violates fkst.ops.invocation.v1"
    if document["ok"] is True:
        if completed.returncode != 0 or not isinstance(document.get("result"), dict):
            return None, "provider success object/exit mismatch"
        result = document["result"]
        if result.get("view") != view or not isinstance(result.get("rows"), list):
            return None, "provider result violates selected contract"
        if view == "engine-durable" and not isinstance(result.get("health"), dict):
            return None, "provider result violates selected contract"
        return result, None
    failure = document.get("failure")
    if completed.returncode not in (1, 2) or not isinstance(failure, dict):
        return None, "provider failure object/exit mismatch"
    if not isinstance(failure.get("code"), str) or not isinstance(failure.get("message"), str) or not isinstance(failure.get("details"), dict):
        return None, "provider failure violates selected contract"
    return None, f"{failure['code']}: {failure['message']}"


def render_plane(name: str, result: dict[str, Any] | None, failure: str | None) -> None:
    print(f"[{name}]")
    if failure is not None:
        print(f"FAIL {name}: {failure}")
        return
    if result is None or result.get("view") != name or not isinstance(result.get("rows"), list):
        print(f"FAIL {name}: result violates selected contract")
        return
    for row in result["rows"]:
        if not isinstance(row, dict):
            print(f"FAIL {name}: malformed row")
            continue
        fields = row.get("fields", {})
        rendered = fields.get("text") if isinstance(fields, dict) else None
        print(rendered if isinstance(rendered, str) else json.dumps(row, sort_keys=True))
    if name == "engine-durable":
        health = result.get("health")
        if isinstance(health, dict):
            print(f"health: {health.get('status', 'unknown')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-provider", required=True)
    parser.add_argument("--engine-provider", required=True)
    parser.add_argument("--github-input", required=True, type=Path)
    parser.add_argument("--engine-input", required=True, type=Path)
    args = parser.parse_args()
    try:
        github_input = json.loads(args.github_input.read_text(encoding="utf-8"))
        engine_input = json.loads(args.engine_input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    github = invoke(args.github_provider, "fkst.ops.board.github-control.v1", "github-control", github_input)
    engine = invoke(args.engine_provider, "fkst.ops.board.engine-durable.v1", "engine-durable", engine_input)
    render_plane("github-control", *github)
    render_plane("engine-durable", *engine)
    return 1 if github[1] is not None or engine[1] is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
