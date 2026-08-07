"""Bounded subprocess helpers for host-run tests."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


COMMAND_TIMEOUT_SECONDS = 60.0


def kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            raise
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise


def run_bounded(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    group_cleanup_complete = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        kill_process_group(process)
        group_cleanup_complete = True
        stdout, stderr = process.communicate(timeout=timeout)
        error.stdout = stdout
        error.stderr = stderr
        raise
    finally:
        if not group_cleanup_complete:
            kill_process_group(process)
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
