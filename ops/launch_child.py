#!/usr/bin/env python3
"""Launch one deployment child with its required process resources."""

from __future__ import annotations

import os
import resource
import subprocess
import sys


MAX_FILES_PER_PROCESS = ("/usr/sbin/sysctl", "-n", "kern.maxfilesperproc")


def required_open_file_limit() -> int:
    value = subprocess.check_output(MAX_FILES_PER_PROCESS, text=True).strip()
    limit = int(value)
    if limit <= 0:
        raise ValueError(f"invalid kern.maxfilesperproc value: {value!r}")
    return limit


def launch(argv: list[str]) -> None:
    if not argv:
        raise ValueError("deployment child command is required")
    try:
        required = required_open_file_limit()
        _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))
        actual, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if actual != required:
            raise RuntimeError(
                f"required open-file limit {required}, loader retained {actual}"
            )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: cannot set required open-file limit: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    os.setsid()
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    launch(sys.argv[1:])
