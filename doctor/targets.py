#!/usr/bin/env python3
"""Emit one tab-separated row per declared deployment for the doctor sweep.

Columns: identity, target checkout, durable root, log root, resolved engine binary.

The engine binary is derived here through `ops.revision_derivation`, the same module
`host/bin_bootstrap.sh` uses, so the binary doctor observes with is the one the deployment
actually runs. Resolving it per target rather than reading a global environment variable is
what lets the durable report run at all from this entrypoint: nothing in production exports
`FKST_OPS_ENGINE_BINARY` on the doctor path.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def resolved_engine_binary(deployment: dict) -> str:
    """Return `<engine_binary>-<derived revision>`, or empty when it cannot be derived."""
    machine = deployment["machine"]
    specification = deployment.get("engine_revision")
    if not isinstance(specification, dict):
        return ""
    path = specification.get("path")
    if not isinstance(path, str) or not path:
        return ""
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "revision_derivation.py"), "resolve",
             machine["target_checkout"], path],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    fields = completed.stdout.strip().split("\t")
    if len(fields) != 2 or not fields[1]:
        return ""
    return f"{machine['engine_binary']}-{fields[1]}"


def main() -> int:
    try:
        resolved = json.load(sys.stdin)
    except ValueError:
        return 0
    for deployment in resolved.get("deployment", []):
        machine = deployment["machine"]
        print("\t".join((
            deployment["id"],
            machine["target_checkout"],
            machine["durable"],
            machine["logs"],
            resolved_engine_binary(deployment),
        )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
