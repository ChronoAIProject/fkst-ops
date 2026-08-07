#!/usr/bin/env python3
"""Behavior tests for the github-devloop host local-iteration gate preflight."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_FIXTURE_ROOT = Path("/Users/auric/fkst-packages")


def shell_quote(value: str | Path) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\\''") + "'"


class LocalIterationCommandHarness:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "host"
        self.root.mkdir()

    def close(self) -> None:
        self.tmp.cleanup()

    def write_executable(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def validate(self, command: str | None, packages: str = "github-proxy github-devloop") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("FKST_DEVLOOP_LOCAL_TEST_COMMAND", None)
        if command is not None:
            env["FKST_DEVLOOP_LOCAL_TEST_COMMAND"] = command
        body = textwrap.dedent(
            f"""\
            set -euo pipefail
            source host/host_run.sh
            HOST_RUN_PROJECT_ROOT={shell_quote(self.root)}
            HOST_RUN_PLATFORM_PACKAGES={shell_quote(packages)}
            HOST_RUN_HOST_PACKAGES=''
            host_run_validate_local_iteration_test_command
            """
        )
        return subprocess.run(
            ["/bin/bash", "-c", body],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def validate_profile(self, profile: Path, packages: str = "github-proxy github-devloop") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("FKST_DEVLOOP_LOCAL_TEST_COMMAND", None)
        body = textwrap.dedent(
            """\
            set -euo pipefail
            source "$1"
            /bin/bash -c '
                set -euo pipefail
                source host/host_run.sh
                HOST_RUN_PROJECT_ROOT="$1"
                HOST_RUN_PLATFORM_PACKAGES="$2"
                HOST_RUN_HOST_PACKAGES=""
                host_run_validate_local_iteration_test_command
              ' host-profile-child "$2" "$3"
            """
        )
        return subprocess.run(
            ["/bin/bash", "-c", body, "host-profile-test", str(profile), str(self.root), packages],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


class HostRunLocalIterationTest(unittest.TestCase):
    def test_activation_validates_before_restarting_the_existing_supervisor(self) -> None:
        source = (REPO_ROOT / "host" / "host_run.sh").read_text(encoding="utf-8")
        validation = "host_run_validate_local_iteration_test_command || return $?"
        restart = "host_run_restart_prior || return $?"

        self.assertIn(validation, source)
        self.assertLess(source.index(validation), source.index(restart))

    def test_package_local_activation_validates_before_creating_runtime_roots(self) -> None:
        source = (HOST_FIXTURE_ROOT / "scripts" / "run.sh").read_text(encoding="utf-8")
        validation = 'host_run_validate_local_iteration_test_command_for "$ROOT" "$pkg"'
        runtime_setup = 'mkdir -p "$rt" "$durable"'

        self.assertIn(validation, source)
        self.assertLess(source.index(validation), source.index(runtime_setup))

    def test_default_command_is_runnable_from_deployed_repository(self) -> None:
        h = LocalIterationCommandHarness()
        try:
            h.write_executable("scripts/run.sh")
            result = h.validate(None)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("local_iteration_test_command=scripts/run.sh test-affected", result.stdout)
        finally:
            h.close()

    def test_missing_default_command_fails_closed(self) -> None:
        h = LocalIterationCommandHarness()
        try:
            result = h.validate(None)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("local iteration test command is not runnable", result.stderr)
            self.assertIn("FKST_DEVLOOP_LOCAL_TEST_COMMAND", result.stderr)
        finally:
            h.close()

    def test_configured_repository_command_is_accepted(self) -> None:
        h = LocalIterationCommandHarness()
        try:
            h.write_executable("tools/preflight")
            result = h.validate("tools/preflight --scope changed")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("local_iteration_test_command=tools/preflight --scope changed", result.stdout)
        finally:
            h.close()

    def test_scaffolded_command_is_inherited_by_child_validator(self) -> None:
        h = LocalIterationCommandHarness()
        try:
            h.write_executable("tools/preflight")
            scaffold = (HOST_FIXTURE_ROOT / "docs" / "user" / "host-profile.env.example").read_text(encoding="utf-8")
            profile_line = next(
                line.removeprefix("# ")
                for line in scaffold.splitlines()
                if line.startswith("# ") and "FKST_DEVLOOP_LOCAL_TEST_COMMAND=" in line
            )
            profile = h.root / "host.env"
            profile.write_text(
                profile_line.replace("make preflight", "tools/preflight --scope changed") + "\n",
                encoding="utf-8",
            )

            result = h.validate_profile(profile)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("local_iteration_test_command=tools/preflight --scope changed", result.stdout)
        finally:
            h.close()

    def test_compound_command_is_rejected_because_runnability_cannot_be_preflighted(self) -> None:
        h = LocalIterationCommandHarness()
        try:
            h.write_executable("tools/preflight")
            result = h.validate("tools/preflight && tools/preflight")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be one executable invocation", result.stderr)
        finally:
            h.close()

    def test_non_devloop_host_does_not_require_local_iteration_command(self) -> None:
        h = LocalIterationCommandHarness()
        try:
            result = h.validate(None, packages="github-proxy idle-detector")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
