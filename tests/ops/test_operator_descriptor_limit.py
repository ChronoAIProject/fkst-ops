#!/usr/bin/env python3
"""Descriptor-limit checks for the deployment child's real loader."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LOADER = ROOT / "ops" / "launch_child.py"
LAUNCHD_SOFT_NOFILE = 256
MAX_FILES_PER_PROCESS = ("/usr/sbin/sysctl", "-n", "kern.maxfilesperproc")


def _run_from_launchd_limit(
    probe: str, environment: dict[str, str], *, restrict_hard_limit: bool = False
) -> subprocess.CompletedProcess[str]:
    bootstrap = textwrap.dedent(
        """\
        import json
        import os
        import resource
        import sys

        _, inherited_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        hard = int(os.environ["TEST_AMBIENT_SOFT"]) if os.environ.get("TEST_RESTRICT_HARD") else inherited_hard
        resource.setrlimit(resource.RLIMIT_NOFILE, (int(os.environ["TEST_AMBIENT_SOFT"]), hard))
        print(json.dumps({"ambient_soft": resource.getrlimit(resource.RLIMIT_NOFILE)[0]}), flush=True)
        os.execv(sys.executable, [sys.executable, os.environ["TEST_LOADER"], sys.executable, "-c", os.environ["TEST_PROBE"]])
        """
    )
    child_environment = {
        **environment,
        "TEST_AMBIENT_SOFT": str(LAUNCHD_SOFT_NOFILE),
        "TEST_LOADER": str(LOADER),
        "TEST_PROBE": probe,
    }
    if restrict_hard_limit:
        child_environment["TEST_RESTRICT_HARD"] = "1"
    return subprocess.run(
        [sys.executable, "-c", bootstrap],
        env=child_environment,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="the deployment loader targets launchd")
def test_real_loader_raises_launchd_limit_and_preserves_composed_environment(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("cargo", "codex"):
        executable = tools / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
    child_path = os.pathsep.join((str(ROOT / "ops"), str(tools), "/usr/bin", "/bin"))
    environment = {
        **os.environ,
        "PATH": child_path,
        "FKST_CARGO": str(tools / "cargo"),
        "FKST_PYTHON": sys.executable,
        "FKST_LAUNCH_PLATFORM_LOCK": str(tmp_path / "platform.lock"),
    }
    probe = textwrap.dedent(
        """\
        import json, os, resource, shutil, sys
        print(json.dumps({
            "child_soft": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
            "path": os.environ["PATH"],
            "cargo": os.environ["FKST_CARGO"],
            "python": os.environ["FKST_PYTHON"],
            "codex": shutil.which("codex"),
            "executable": sys.executable,
            "own_session": os.getpid() == os.getsid(0) == os.getpgrp(),
        }), flush=True)
        """
    )

    result = _run_from_launchd_limit(probe, environment)

    assert result.returncode == 0, result.stdout + result.stderr
    ambient, child = (json.loads(line) for line in result.stdout.splitlines())
    expected = int(subprocess.check_output(MAX_FILES_PER_PROCESS, text=True))
    assert ambient == {"ambient_soft": LAUNCHD_SOFT_NOFILE}
    assert child == {
        "child_soft": expected,
        "path": child_path,
        "cargo": str(tools / "cargo"),
        "python": sys.executable,
        "codex": str(tools / "codex"),
        "executable": sys.executable,
        "own_session": True,
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="the deployment loader targets launchd")
def test_real_loader_refuses_to_start_when_required_limit_cannot_be_set(tmp_path: Path) -> None:
    child_marker = "target-child-started"
    environment = {**os.environ, "FKST_LAUNCH_PLATFORM_LOCK": str(tmp_path / "platform.lock")}
    result = _run_from_launchd_limit(
        f"print({child_marker!r}, flush=True)", environment, restrict_hard_limit=True
    )

    assert result.returncode != 0
    assert json.loads(result.stdout.splitlines()[0]) == {
        "ambient_soft": LAUNCHD_SOFT_NOFILE
    }
    assert child_marker not in result.stdout
    assert "cannot set required open-file limit" in result.stderr
