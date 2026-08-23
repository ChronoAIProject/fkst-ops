#!/usr/bin/env python3
"""Read-only doctor probe for one declared deployment's supervise process."""

from __future__ import annotations

import argparse
import json
import time

from ops.deployment_process import probe_deployment_process
from ops.probe_result import ProbeFailure, ProbeResult
from schema.validator import ValidationError, load_and_resolve


def _resolution_failure(deployment_id: str, message: str) -> ProbeResult:
    failure = ProbeFailure("resolution_failure", message, requested_deployment=deployment_id)
    return ProbeResult(
        "deployment_process",
        "unknown",
        {"deployment": None, "target_identity": None, "project_root": None, "durable_root": None},
        {"pid": None},
        {"basis": "unix_epoch_ns", "now": time.time_ns(), "process_started": None,
         "clock": "time.time_ns"},
        {"configuration": "schema.validator", "pid_claim": None, "process_instrument": None},
        {"scope": "one requested deployment", "complete": False, "truncated": False,
         "selector_validated": False, "time_conversion_validated": False},
        failure.as_dict(),
    )


def run(declaration: str, machine_profile: str, lock: str, deployment_id: str) -> int:
    try:
        resolved = load_and_resolve(declaration, machine_profile, lock)
    except ValidationError as exc:
        result = _resolution_failure(deployment_id, str(exc))
    else:
        deployment = next((item for item in resolved["deployment"] if item["id"] == deployment_id), None)
        if deployment is None:
            result = _resolution_failure(deployment_id, f"deployment id is not declared: {deployment_id}")
        else:
            result = probe_deployment_process(deployment)
    print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.state in {"present", "absent"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--declaration", required=True)
    parser.add_argument("--machine-profile", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("deployment_id")
    args = parser.parse_args(argv)
    return run(args.declaration, args.machine_profile, args.lock, args.deployment_id)


if __name__ == "__main__":
    raise SystemExit(main())
