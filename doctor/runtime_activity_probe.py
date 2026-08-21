#!/usr/bin/env python3
"""Read-only doctor probe for deployment runtime artifact activity."""

from __future__ import annotations

import argparse
import json
import time

from ops.probe_result import ProbeFailure, ProbeResult
from ops.runtime_activity import probe_runtime_activity
from schema.validator import ValidationError, load_and_resolve


def _resolution_failure(deployment_id: str, message: str) -> ProbeResult:
    failure = ProbeFailure("resolution_failure", message, requested_deployment=deployment_id)
    return ProbeResult(
        probe="runtime_artifact_activity",
        state="unknown",
        identity={"deployment": None, "target_identity": None, "runtime_root": None},
        observations={"matching_count": 0, "latest_path": None, "latest_modified": None},
        time={"basis": "unix_epoch_ns", "now": time.time_ns(), "window_start": None,
              "clock": "time.time_ns"},
        provenance={"configuration": "schema.validator", "filesystem_instrument": None},
        coverage={"scope": "one requested deployment runtime root", "complete": False,
                  "truncated": False, "entries_examined": 0, "selector_validated": False,
                  "time_conversion_validated": False},
        failure=failure.as_dict(),
    )


def run(
    declaration: str,
    machine_profile: str,
    lock: str,
    deployment_id: str,
    since_epoch_ns: str,
) -> int:
    try:
        resolved = load_and_resolve(declaration, machine_profile, lock)
    except ValidationError as exc:
        result = _resolution_failure(deployment_id, str(exc))
    else:
        deployment = next(
            (item for item in resolved["deployment"] if item["id"] == deployment_id), None
        )
        if deployment is None:
            result = _resolution_failure(
                deployment_id, f"deployment id is not declared: {deployment_id}"
            )
        else:
            result = probe_runtime_activity(deployment, since_epoch_ns)
    print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.state in {"present", "absent"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--declaration", required=True)
    parser.add_argument("--machine-profile", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--since-epoch-ns", required=True)
    parser.add_argument("deployment_id")
    args = parser.parse_args(argv)
    return run(
        args.declaration, args.machine_profile, args.lock,
        args.deployment_id, args.since_epoch_ns,
    )


if __name__ == "__main__":
    raise SystemExit(main())
