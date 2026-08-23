#!/usr/bin/env python3
"""Focused checks for operator-owned source synchronization."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
SOURCE_CONTROL = ROOT / "ops" / "deployment_source_control.sh"


class OperatorSourceControlTest(unittest.TestCase):
    def test_sync_to_run_branch_propagates_fetch_and_checkout_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            fake_git = tools / "git"
            fake_git.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = -C ] && shift 2\n"
                "case \"$MODE:$*\" in\n"
                "  fetch:rev-parse*--git-dir*) exit 0;;\n"
                "  fetch:fetch*) exit 9;;\n"
                "  checkout:rev-parse*--git-dir*) exit 0;;\n"
                "  checkout:fetch*) exit 0;;\n"
                "  checkout:rev-parse*origin/integration*) echo abc; exit 0;;\n"
                "  checkout:checkout*) exit 8;;\n"
                "  checkout:reset*) exit 8;;\n"
                "  checkout:clean*) exit 0;;\n"
                "  checkout:rev-parse*HEAD*) echo abc; exit 0;;\n"
                "esac\n"
                "exit 1\n",
                encoding="ascii",
            )
            fake_git.chmod(0o755)
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^sync_to_run_branch()/,/^}}/p' "{SOURCE_CONTROL}")"
INTEGRATION_BRANCH=integration
sync_to_run_branch /checkout
'''
            for mode, marker, status in (
                ("fetch", "FETCH-FAILED", 1), ("checkout", "CHECKOUT-FAILED", 8)
            ):
                environment = {
                    **os.environ,
                    "MODE": mode,
                    "PATH": f"{tools}:{os.environ['PATH']}",
                }
                result = subprocess.run(
                    ["bash", "-c", command], env=environment, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertIn(marker, result.stdout)

    def test_pinned_source_enforces_head_without_policing_tracked_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Test"], check=True,
            )
            (source / "state").write_text("pinned\n", encoding="ascii")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "pinned"], check=True)
            pinned = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(source), "branch", "integration"], check=True)
            checkout = root / "checkout"
            subprocess.run(["git", "clone", "-q", str(source), str(checkout)], check=True)
            (source / "state").write_text("advanced\n", encoding="ascii")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-qm", "advance integration"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "branch", "-f", "integration", "HEAD"],
                check=True,
            )
            advanced = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "integration"], text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", str(checkout), "fetch", "-q", "origin", "integration"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(checkout), "checkout", "-q", "-B", "integration",
                    "origin/integration",
                ],
                check=True,
            )
            self.assertEqual(
                advanced,
                subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                    capture_output=True, check=True,
                ).stdout.strip(),
            )
            command = f'''\
eval "$(sed -n '/^sync_to_pinned_revision()/,/^}}/p' "{SOURCE_CONTROL}")"
sync_to_pinned_revision "$1" "$2"
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), pinned],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(
                pinned,
                subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                    capture_output=True, check=True,
                ).stdout.strip(),
            )
            self.assertEqual(
                "",
                subprocess.run(
                    ["git", "-C", str(checkout), "branch", "--show-current"], text=True,
                    capture_output=True, check=True,
                ).stdout.strip(),
            )
            self.assertNotEqual(pinned, advanced)
            self.assertIn("(pinned", result.stdout)

            (checkout / "state").write_text("operator edit\n", encoding="ascii")
            repeated = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), pinned],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
            self.assertEqual("operator edit\n", (checkout / "state").read_text(encoding="ascii"))

    def test_pinned_source_failure_is_typed_and_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Test"], check=True,
            )
            (source / "state").write_text("branch\n", encoding="ascii")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "branch"], check=True)
            checkout = root / "checkout"
            subprocess.run(["git", "clone", "-q", str(source), str(checkout)], check=True)
            before = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            missing = "f" * 40
            command = f'''\
eval "$(sed -n '/^sync_to_pinned_revision()/,/^}}/p' "{SOURCE_CONTROL}")"
sync_to_pinned_revision "$1" "$2"
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), missing],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("PINNED-FETCH-FAILED", result.stdout)
            self.assertEqual(
                before,
                subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                    capture_output=True, check=True,
                ).stdout.strip(),
            )

    def test_sync_never_touches_hydrated_mechanism_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pinned = (
                Path(directory) / ".fkst" / "run" / "fkst-ops" / "checkouts" / ("a" * 40)
            )
            (pinned / "ops").mkdir(parents=True)
            head = pinned / "HEAD"
            head.write_text("a" * 40, encoding="ascii")
            command = f'''set -uo pipefail
eval "$(sed -n '/^cmd_sync()/,/^}}/p' "{OPERATOR}")"
expand() {{ printf 'deployment-a\\n'; }}
cfg() {{
  UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
  HOST=/target; PKGSRC=/platform; RUNTIME_ROOT=/runtime; ENGINE_CHECKOUT=/engine; BIN=/engine/bin
  ENGINE_PROVIDER=/provider; ENGINE_CONTRACT=contract; ENGINE_PROVIDER_CONFIGURATION='{{}}'
}}
git_lock_sweep() {{ :; }}
ensure_integration_caught_up() {{ :; }}
sync_to_run_branch() {{ printf 'synced:%s\\n' "$1"; }}
sync_declared_package_sources() {{ :; }}
declared_package_source_checkouts() {{ :; }}
ensure_declared_package_source_checkouts() {{ :; }}
bin_ensure_fresh() {{ echo built; }}
_proc_stale() {{ echo current; }}
restart_one() {{ echo unexpected-restart; return 1; }}
cmd_sync all
'''
            result = subprocess.run(
                ["bash", "-c", command], text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("a" * 40, head.read_text(encoding="ascii"))
            self.assertNotIn("operator checkouts", result.stdout)
            self.assertIn("synced:/platform", result.stdout)
            self.assertIn("synced:/target", result.stdout)
            self.assertNotIn("unexpected-restart", result.stdout)


if __name__ == "__main__":
    unittest.main()
