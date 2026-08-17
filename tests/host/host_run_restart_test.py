#!/usr/bin/env python3
"""Restart-process behavior tests for host/host_run.sh."""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from ops.revision_derivation import write_build_receipt

from host_run_fixture import (
    HostRunHarness,
    kill_if_alive,
    pid_is_alive,
    shell_quote,
    start_orphan_sleep,
    wait_for_dead,
)


class HostRunRestartTest(unittest.TestCase):
    def test_mutated_revision_binary_fails_at_final_consumer_check(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = "a" * 40
            binary = root / f"engine-{revision}"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            binary.chmod(0o755)
            write_build_receipt(binary, revision, ["cargo", "build", "-p", "engine"])
            binary.write_text("#!/bin/sh\nexit 9\n", encoding="ascii")
            binary.chmod(0o755)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    textwrap.dedent(
                        f"""\
                        set -euo pipefail
                        source host/host_run.sh
                        BIN={shell_quote(binary)}
                        HOST_RUN_PACKAGE_ROOTS=(/platform/pkg)
                        host_run_validate_shape() {{ return 0; }}
                        host_run_build_package_roots() {{ return 0; }}
                        host_run_validate_local_iteration_test_command() {{ return 0; }}
                        host_run_restart_prior() {{ return 0; }}
                        host_run_export_codex_repository_roots() {{ return 0; }}
                        host_run_claim_supervise_slot() {{ return 0; }}
                        host_run_supervise_contract --project-root /project --platform-root /platform --platform-packages pkg --durable-root {shell_quote(root)} --expected-engine-revision {revision}
                        """
                    ),
                ],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ENGINE_BINARY_RECEIPT_MISMATCH", result.stderr)

    def test_crossed_expected_revision_fails_before_restart_or_claim(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "side-effect"
            expected = "1" * 40
            other = "2" * 40
            binary = root / f"engine-{other}"
            binary.write_text("#!/bin/sh\n", encoding="ascii")
            binary.chmod(0o755)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    textwrap.dedent(
                        f"""\
                        set -euo pipefail
                        source host/host_run.sh
                        BIN={shell_quote(binary)}
                        host_run_parse_supervise_args --project-root /project --platform-root /platform --platform-packages pkg --durable-root /durable --expected-engine-revision {expected}
                        host_run_validate_shape() {{ return 0; }}
                        host_run_build_package_roots() {{ return 0; }}
                        host_run_validate_local_iteration_test_command() {{ return 0; }}
                        host_run_restart_prior() {{ printf restart > {shell_quote(marker)}; }}
                        host_run_claim_supervise_slot() {{ printf claim > {shell_quote(marker)}; }}
                        host_run_supervise_contract --project-root /project --platform-root /platform --platform-packages pkg --durable-root /durable --expected-engine-revision {expected}
                        """
                    ),
                ],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertIn("ENGINE_REVISION_MISMATCH", result.stderr)

    def test_missing_binary_fails_before_restart_or_claim(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "side-effect"
            expected = "a" * 40
            missing = root / f"missing-engine-{expected}"
            result = subprocess.run(
                ["/bin/bash", "-c",
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    BIN={shell_quote(missing)}
                    HOST_RUN_EXPECTED_ENGINE_REVISION={expected}
                    host_run_parse_supervise_args() {{ return 0; }}
                    host_run_validate_shape() {{ return 0; }}
                    host_run_build_package_roots() {{ return 0; }}
                    host_run_validate_local_iteration_test_command() {{ return 0; }}
                    host_run_restart_prior() {{ printf restart > {shell_quote(marker)}; }}
                    host_run_claim_supervise_slot() {{ printf claim > {shell_quote(marker)}; }}
                    host_run_supervise_contract
                    """
                )],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertIn(
                f"ENGINE_BINARY_UNAVAILABLE: declared build path: {missing}",
                result.stderr,
            )
            source = (repo_root / "host" / "host_run.sh").read_text(encoding="utf-8")
            function = source[source.index("host_run_supervise_contract() {") :]
            self.assertLess(
                function.index("host_run_require_expected_engine_revision"),
                function.index("host_run_restart_prior"),
            )
            self.assertLess(
                function.index("host_run_require_engine_binary"),
                function.index("host_run_restart_prior"),
            )
            self.assertLess(
                function.rindex("host_run_require_engine_binary"),
                function.index("host_run_claim_supervise_slot"),
            )
    def test_restart_kills_pid_file_process_without_command_text_matching(self) -> None:
        h = HostRunHarness()
        pid = start_orphan_sleep()
        try:
            h.durable.mkdir()
            (h.durable / ".fkst-supervise.pid").write_text(str(pid) + "\n", encoding="utf-8")
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.substrate_host)} --platform-root {shell_quote(h.platform)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)} --restart
                    host_run_validate_shape
                    host_run_restart_prior
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(wait_for_dead(pid), f"pid {pid} still alive")
            self.assertFalse((h.durable / ".fkst-supervise.pid").exists())
            self.assertIn("killing prior supervise pid", result.stderr)
        finally:
            kill_if_alive(pid)
            h.close()

    def test_restart_fails_closed_when_prior_cannot_be_killed(self) -> None:
        h = HostRunHarness()
        pid = start_orphan_sleep()
        try:
            h.durable.mkdir()
            pidfile = h.durable / ".fkst-supervise.pid"
            pidfile.write_text(str(pid) + "\n", encoding="utf-8")
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    kill() {{
                      if [ "${{1:-}}" = "-9" ]; then
                        return 1
                      fi
                      command kill "$@"
                    }}
                    host_run_parse_supervise_args --project-root {shell_quote(h.substrate_host)} --platform-root {shell_quote(h.platform)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)} --restart
                    host_run_validate_shape
                    host_run_restart_prior
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(pid_is_alive(pid), f"pid {pid} should not have been killed")
            self.assertEqual(pidfile.read_text(encoding="utf-8").strip(), str(pid))
            self.assertIn("failed to SIGKILL prior supervise pid", result.stderr)
        finally:
            kill_if_alive(pid)
            h.close()

    def test_launch_without_restart_fails_closed_when_pidfile_is_live(self) -> None:
        h = HostRunHarness()
        pid = start_orphan_sleep()
        try:
            h.durable.mkdir()
            (h.durable / ".fkst-supervise.pid").write_text(str(pid) + "\n", encoding="utf-8")
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.substrate_host)} --platform-root {shell_quote(h.platform)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_claim_supervise_slot
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(pid_is_alive(pid), f"pid {pid} should still be alive")
            self.assertIn("is still running for durable root", result.stderr)
        finally:
            kill_if_alive(pid)
            h.close()


if __name__ == "__main__":
    unittest.main()
