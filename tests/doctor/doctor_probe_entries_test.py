"""The typed question families remain subcommands of the doctor entry."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "doctor" / "doctor.sh"


class DoctorProbeEntriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fake_python = Path(self.temp.name) / "python"
        self.fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="ascii")
        self.fake_python.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment.update({
            "FKST_OPS_PYTHON": str(self.fake_python),
            "FKST_OPS_DECLARATION": "/control/declaration.toml",
            "FKST_OPS_MACHINE_PROFILE": "/control/machine.toml",
            "FKST_OPS_LOCK": "/control/lock.toml",
        })

    def tearDown(self) -> None:
        self.temp.cleanup()

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DOCTOR), *arguments], env=self.environment,
            text=True, capture_output=True, check=False,
        )

    def test_runtime_activity_dispatches_to_probe(self) -> None:
        result = self.invoke(
            "runtime-activity", "--since-epoch-ns", "1700000000000000000", "deployment-a"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doctor/runtime_activity_probe.py", result.stdout)
        self.assertIn("1700000000000000000", result.stdout)

    def test_completion_dispatches_to_probe(self) -> None:
        result = self.invoke("completion", "/runtime/attempt-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doctor/completion_probe.py", result.stdout)
        self.assertIn("/runtime/attempt-1", result.stdout)

    def test_github_activity_dispatches_to_probe(self) -> None:
        result = self.invoke(
            "github-activity", "--since-utc", "2026-01-01T00:00:00Z", "deployment-a", "17"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doctor/github_activity_probe.py", result.stdout)
        self.assertIn("2026-01-01T00:00:00Z", result.stdout)


if __name__ == "__main__":
    unittest.main()
