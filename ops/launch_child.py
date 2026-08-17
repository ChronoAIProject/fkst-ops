#!/usr/bin/env python3
"""Launch one deployment child with its required process resources."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys


MAX_FILES_PER_PROCESS = ("/usr/sbin/sysctl", "-n", "kern.maxfilesperproc")


def required_open_file_limit() -> int:
    value = subprocess.check_output(MAX_FILES_PER_PROCESS, text=True).strip()
    limit = int(value)
    if limit <= 0:
        raise ValueError(f"invalid kern.maxfilesperproc value: {value!r}")
    return limit


def hold_launch_platform() -> None:
    lock_path = os.environ.pop("FKST_LAUNCH_PLATFORM_LOCK", "")
    if not lock_path:
        raise ValueError("launch platform lock is required")
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    os.set_inheritable(descriptor, True)


def remove_unlocked_snapshot(snapshot: Path, lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"LAUNCH_PLATFORM_IN_USE: retaining {snapshot}", file=sys.stderr)
            return 75
        if snapshot.exists():
            shutil.rmtree(snapshot)
    return 0


def launch(argv: list[str]) -> None:
    if not argv:
        raise ValueError("deployment child command is required")
    try:
        hold_launch_platform()
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
    if len(sys.argv) == 4 and sys.argv[1] == "--remove-unlocked-snapshot":
        raise SystemExit(remove_unlocked_snapshot(Path(sys.argv[2]), Path(sys.argv[3])))
    launch(sys.argv[1:])
