#!/usr/bin/env python3
"""Read-only doctor probe for activity on one exact GitHub entity."""

from __future__ import annotations

import argparse
import json
import time

from ops.github_activity import probe_github_activity
from ops.probe_result import ProbeFailure, ProbeResult
from schema.validator import ValidationError, load_and_resolve


def _resolution_failure(deployment_id: str, message: str) -> ProbeResult:
    failure = ProbeFailure("resolution_failure", message, requested_deployment=deployment_id)
    return ProbeResult(
        probe="github_activity",
        state="unknown",
        identity={"deployment": None, "target_identity": None, "repository": None,
                  "entity_number": None, "entity_node_id": None, "entity_url": None,
                  "entity_kind": None},
        observations={"activity_at": None},
        time={"basis": "unix_epoch_ns", "now": time.time_ns(), "since": None,
              "since_utc": None, "entity_updated": None, "clock": "time.time_ns"},
        provenance={"configuration": "schema.validator", "github_instrument": None},
        coverage={"scope": "one requested GitHub entity updated_at", "complete": False,
                  "truncated": False, "pages": 0, "selector_validated": False,
                  "time_conversion_validated": False},
        failure=failure.as_dict(),
    )


def run(
    declaration: str,
    machine_profile: str,
    lock: str,
    deployment_id: str,
    entity_number: str,
    since_utc: str,
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
            result = probe_github_activity(deployment, entity_number, since_utc)
    print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.state in {"present", "absent"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--declaration", required=True)
    parser.add_argument("--machine-profile", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--since-utc", required=True)
    parser.add_argument("deployment_id")
    parser.add_argument("entity_number")
    args = parser.parse_args(argv)
    return run(
        args.declaration, args.machine_profile, args.lock, args.deployment_id,
        args.entity_number, args.since_utc,
    )


if __name__ == "__main__":
    raise SystemExit(main())
