#!/usr/bin/env python3
"""Read-only doctor probe for one engine run's producer-defined completion state."""

from __future__ import annotations

import argparse
import json

from ops.engine_completion import probe_engine_completion


def run(run_root: str) -> int:
    result = probe_engine_completion(run_root)
    print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.state in {"present", "absent"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root")
    args = parser.parse_args(argv)
    return run(args.run_root)


if __name__ == "__main__":
    raise SystemExit(main())
