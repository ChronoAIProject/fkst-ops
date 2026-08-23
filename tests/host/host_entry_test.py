#!/usr/bin/env python3
"""Behavior tests for platform-only host entry helpers."""

from __future__ import annotations

import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def shell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


class HostEntryTest(unittest.TestCase):
    def test_test_cleanup_exit_traps_also_disarm_the_watchdog(self) -> None:
        source = (REPO_ROOT / "host" / "host_entry.sh").read_text(encoding="utf-8")
        for body in re.findall(r"trap '([^']*)' EXIT", source):
            if re.search(r"TEST_HERMETIC_RUNTIME_ROOT|HOST_TEST_RUNTIME_ROOT", body):
                self.assertIn("disarm_test_deadline", body)

    def test_supervise_forwards_only_declared_platform_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            durable = root / "durable"
            package = platform / "packages" / "github-proxy"
            package.mkdir(parents=True)
            (host / ".fkst" / "compose").mkdir(parents=True)
            (host / ".fkst" / "compose" / "package-roots").write_text(
                "platform:packages/github-proxy\n", encoding="utf-8"
            )
            command = textwrap.dedent(
                f"""\
                set -euo pipefail
                ROOT={shell_quote(REPO_ROOT)}
                source host/bin_bootstrap.sh
                source host/host_run.sh
                source host/host_entry.sh
                resolve_bin() {{ BIN=/tmp/fake-bin; export BIN; }}
                ensure_fresh_bin() {{ :; }}
                host_run_supervise_contract() {{ printf '%s\\n' "$@"; }}
                cmd_host --host-root {shell_quote(host)} --platform-root {shell_quote(platform)} -- \
                  supervise --durable-root {shell_quote(durable)}
                """
            )
            result = subprocess.run(
                ["/bin/bash", "-c", command],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "--project-root", str(host),
                    "--platform-root", str(platform),
                    "--platform-packages", "github-proxy",
                    "--durable-root", str(durable),
                ],
            )


if __name__ == "__main__":
    unittest.main()
