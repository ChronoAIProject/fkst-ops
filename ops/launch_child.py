#!/usr/bin/env python3
"""Launch one deployment child with its required process resources."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys


MAX_FILES_PER_PROCESS = ("/usr/sbin/sysctl", "-n", "kern.maxfilesperproc")


def required_open_file_limit() -> int:
    value = subprocess.check_output(MAX_FILES_PER_PROCESS, text=True).strip()
    limit = int(value)
    if limit <= 0:
        raise ValueError(f"invalid kern.maxfilesperproc value: {value!r}")
    return limit


def hold_inherited_lock(environment_name: str) -> None:
    value = os.environ.pop(environment_name, "")
    if not value:
        raise ValueError(f"inherited descriptor {environment_name} is required")
    try:
        descriptor = int(value)
    except ValueError as exc:
        raise ValueError(f"inherited descriptor {environment_name} is invalid") from exc
    os.fstat(descriptor)
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    os.set_inheritable(descriptor, True)


def hold_launch_locks() -> None:
    hold_inherited_lock("FKST_LAUNCH_PLATFORM_LOCK_FD")
    hold_inherited_lock("FKST_LAUNCH_ENGINE_LOCK_FD")


def open_shared_revision_lock(lock_path: Path, guard_path: Path) -> int:
    guard_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with guard_path.open("a+b") as guard:
        fcntl.flock(guard, fcntl.LOCK_SH)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
    os.set_inheritable(descriptor, True)
    return descriptor


def spawn_locked_child(
    log_path: Path,
    platform_lock: Path,
    platform_guard: Path,
    engine_lock: Path,
    engine_guard: Path,
    argv: list[str],
) -> int:
    try:
        platform_descriptor = open_shared_revision_lock(platform_lock, platform_guard)
        engine_descriptor = open_shared_revision_lock(engine_lock, engine_guard)
        log_descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
        pid = os.fork()
    except OSError as error:
        print(f"error: cannot create locked launch child: {error}", file=sys.stderr)
        return 1
    if pid:
        os.close(platform_descriptor)
        os.close(engine_descriptor)
        os.close(log_descriptor)
        print(pid)
        return 0
    os.dup2(log_descriptor, 1)
    os.dup2(log_descriptor, 2)
    os.close(log_descriptor)
    os.environ["FKST_LAUNCH_PLATFORM_LOCK_FD"] = str(platform_descriptor)
    os.environ["FKST_LAUNCH_ENGINE_LOCK_FD"] = str(engine_descriptor)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    launch(argv)
    raise AssertionError("launch returned after exec")


def remove_unlocked_snapshot(snapshot: Path, lock_path: Path, guard_path: Path) -> int:
    guard_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with guard_path.open("a+b") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        with lock_path.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"LAUNCH_PLATFORM_IN_USE: retaining {snapshot}", file=sys.stderr)
                return 75
            if snapshot.exists():
                shutil.rmtree(snapshot)
            lock_path.unlink(missing_ok=True)
    return 0


def launch(argv: list[str]) -> None:
    if not argv:
        raise ValueError("deployment child command is required")
    try:
        hold_launch_locks()
    except (OSError, ValueError) as error:
        print(f"error: cannot hold inherited launch locks: {error}", file=sys.stderr)
        raise SystemExit(1) from error
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
    if len(sys.argv) >= 8 and sys.argv[1] == "--spawn":
        raise SystemExit(
            spawn_locked_child(
                Path(sys.argv[2]),
                Path(sys.argv[3]),
                Path(sys.argv[4]),
                Path(sys.argv[5]),
                Path(sys.argv[6]),
                sys.argv[7:],
            )
        )
    if len(sys.argv) == 5 and sys.argv[1] == "--remove-unlocked-snapshot":
        raise SystemExit(
            remove_unlocked_snapshot(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        )
    launch(sys.argv[1:])
