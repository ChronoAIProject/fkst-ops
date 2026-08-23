#!/usr/bin/env python3
"""Bounded-runner behavior retained from the former launch golden harness."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from host_run_test_support import run_bounded


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def wait_for_process_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


class HostRunEquivalenceTest(unittest.TestCase):
    def test_external_commands_cannot_bypass_bounded_runner(self) -> None:
        self.assertNotIn("subprocess" + ".run(", Path(__file__).read_text(encoding="utf-8"))

    def test_bounded_runner_kills_process_tree_on_timeout_and_parent_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "child-tree.sh"
            write_executable(
                script,
                "#!/usr/bin/env bash\nsleep 60 >/dev/null 2>&1 &\n"
                "printf '%s %s\\n' \"$$\" \"$!\" > \"$1\"\n"
                "[ \"$2\" != timeout ] || wait\n",
            )
            for mode in ("timeout", "success"):
                pid_file = root / f"{mode}.pids"
                pids: list[int] = []
                try:
                    if mode == "timeout":
                        with self.assertRaises(subprocess.TimeoutExpired):
                            run_bounded(
                                [str(script), str(pid_file), mode],
                                cwd=root,
                                env=os.environ.copy(),
                                timeout=3.0,
                            )
                    else:
                        result = run_bounded(
                            [str(script), str(pid_file), mode],
                            cwd=root,
                            env=os.environ.copy(),
                            timeout=5.0,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                    pids = [int(value) for value in pid_file.read_text(encoding="utf-8").split()]
                    self.assertEqual(len(pids), 2)
                    for pid in pids:
                        self.assertTrue(
                            wait_for_process_exit(pid),
                            f"process {pid} survived {mode} cleanup",
                        )
                finally:
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main()
