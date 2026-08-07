#!/usr/bin/env python3
"""Restart-process behavior tests for host/host_run.sh."""

from __future__ import annotations

import textwrap
import unittest

from host_run_fixture import (
    HostRunHarness,
    kill_if_alive,
    pid_is_alive,
    shell_quote,
    start_orphan_sleep,
    wait_for_dead,
)


class HostRunRestartTest(unittest.TestCase):
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
