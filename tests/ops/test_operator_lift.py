#!/usr/bin/env python3
"""Focused checks for the declaration-backed operator lift."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "dogfood.sh"
MANIFEST = ROOT / "ops" / "workspace_manifest.py"


class OperatorLiftTest(unittest.TestCase):
    def test_shell_is_valid_and_uses_schema_validator(self) -> None:
        subprocess.run(["bash", "-n", str(OPERATOR)], check=True)
        source = OPERATOR.read_text(encoding="utf-8")
        self.assertIn("python3 -m schema.validator", source)
        self.assertIn('rt="$RUNTIME_ROOT/${name}.${ts}"', source)
        self.assertNotIn("GH_ORG=", source)
        self.assertNotIn("GITHUB_PROXY_POLL_LABEL_PREFIX=", source)
        self.assertNotIn("  doctor)", source)

    def test_platform_source_role_is_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "chosen-platform"\npackages = ["one", "two"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(MANIFEST),
                    "platform-packages",
                    "deployment-a",
                    str(host),
                    str(platform),
                    "chosen-platform",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "one two")

    def test_unknown_platform_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "other"\npackages = ["one"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(MANIFEST),
                    "platform-packages",
                    "deployment-a",
                    str(host),
                    str(platform),
                    "chosen-platform",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("chosen-platform", result.stdout)

    def test_corrupt_run_checkout_is_recloned_from_resolved_git_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote"
            checkout = root / "run"
            remote.mkdir()
            subprocess.run(["git", "init", "-q", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(remote), "config", "user.name", "Test"], check=True)
            (remote / "tracked").write_text("restored\n", encoding="ascii")
            subprocess.run(["git", "-C", str(remote), "add", "tracked"], check=True)
            subprocess.run(["git", "-C", str(remote), "commit", "-qm", "fixture"], check=True)
            checkout.mkdir()
            (checkout / "partial").write_text("corrupt\n", encoding="ascii")
            command = f'''set -e
eval "$(sed -n '/^ensure_run_checkout()/,/^}}/p' {OPERATOR})"
ensure_run_checkout "$1" "$2"
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), str(remote)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((checkout / "tracked").read_text(encoding="ascii"), "restored\n")
            self.assertTrue(list(root.glob("run.corrupt.*")))


if __name__ == "__main__":
    unittest.main()
