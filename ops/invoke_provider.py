#!/usr/bin/env python3
"""Invoke one direct fkst-ops provider and validate its common envelope."""

import json
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: invoke_provider.py EXECUTABLE CONTRACT", file=sys.stderr)
        return 2
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError) as exc:
        print(f"invalid provider input: {exc}", file=sys.stderr)
        return 2
    request = {"version": "fkst.ops.invocation.v1", "contract": sys.argv[2], "input": payload}
    try:
        run = subprocess.run([sys.argv[1]], input=json.dumps(request), text=True, capture_output=True)
    except OSError as exc:
        print(f"provider invocation failed: {exc}", file=sys.stderr)
        return 1
    if run.stderr:
        sys.stderr.write(run.stderr)
    try:
        decoder = json.JSONDecoder()
        result, end = decoder.raw_decode(run.stdout)
        if run.stdout[end:].strip():
            raise ValueError("multiple JSON documents")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"invalid provider output: {exc}", file=sys.stderr)
        return 1
    valid = isinstance(result, dict) and result.get("version") == "fkst.ops.invocation.v1" and isinstance(result.get("ok"), bool)
    if not valid or (result["ok"] and run.returncode != 0) or (not result["ok"] and run.returncode not in (1, 2)):
        print("provider output/exit violates invocation contract", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0 if result["ok"] else run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
