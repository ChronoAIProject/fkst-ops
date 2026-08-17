#!/usr/bin/env python3
"""Credential, status, workspace, and checkout boundary tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
MANIFEST = ROOT / "ops" / "workspace_manifest.py"


class OperatorBoundaryTest(unittest.TestCase):
    def test_refreshed_credential_is_secret_and_identity_checked_every_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "never-expose-this-token"
            helper = root / "helper"
            helper.write_text(
                f'''#!/bin/sh
printf '%s\\n' '{{"login":"wrong-bot","token":"{token}"}}'
''', encoding="ascii",
            )
            helper.chmod(0o755)
            args = root / "args"
            real_gh = root / "real-gh"
            real_gh.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{args}"\n', encoding="ascii")
            real_gh.chmod(0o755)
            env = {**os.environ, "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
                   "FKST_GITHUB_CREDENTIAL_SOURCE": "github-app",
                   "FKST_GITHUB_BOT_LOGIN": "declared-bot", "FKST_GITHUB_REAL_GH": str(real_gh)}
            result = subprocess.run(
                [sys.executable, str(ROOT / "ops/github_credential_gh.py"), "api", "/repo"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HEALTH=UNHEALTHY", result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertFalse(args.exists())
            for artifact in root.rglob("*"):
                if artifact.is_file() and artifact not in {helper}:
                    self.assertNotIn(token, artifact.read_text(encoding="utf-8"))

    def test_credential_never_appears_in_real_gh_process_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "process-argument-secret"
            helper = root / "helper"
            helper.write_text(
                f'#!/bin/sh\nprintf \'%s\\n\' \'{{"login":"declared-bot","token":"{token}","target":"example/repo","identity_proof":"target-access-only;bot-login-not-mechanically-proven"}}\'\n',
                encoding="ascii",
            )
            helper.chmod(0o755)
            arguments = root / "arguments"
            real_gh = root / "real-gh"
            real_gh.write_text(
                f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{arguments}"\n', encoding="ascii",
            )
            real_gh.chmod(0o755)
            env = {**os.environ, "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
                   "FKST_GITHUB_CREDENTIAL_SOURCE": "github-app",
                   "FKST_GITHUB_BOT_LOGIN": "declared-bot", "FKST_GITHUB_REAL_GH": str(real_gh),
                   "FKST_GITHUB_REPO": "example/repo"}
            result = subprocess.run(
                [sys.executable, str(ROOT / "ops/github_credential_gh.py"), "api", "/repo"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(arguments.read_text(encoding="ascii"), "api\n/repo\n")
            self.assertNotIn(token, result.stdout + result.stderr + arguments.read_text(encoding="ascii"))

    def test_authentication_failure_is_visible_in_status_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "FKST_GITHUB_WRITE=1 FKST_GITHUB_WRITER_LOGIN=resolved-bot "
                "FKST_GITHUB_CLAIM_MODE=label FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=0\n"
                "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY\n",
                encoding="ascii",
            )
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^status_one()/,/^}}/p' "{OPERATOR}")"
cfg() {{ HOST=/host; PKGSRC=/platform; REPO=example/repo; }}
pidof_df() {{ echo 123; }}; latest_log() {{ echo "$LOG"; }}
fmt_uptime() {{ echo 1m00s; }}; engine_panic_count() {{ echo 0; }}
ps() {{ echo 00:01:00; }}; git() {{ echo abcdef123456; }}
status_one fixture
'''
            result = subprocess.run(["bash", "-c", command], env={**os.environ, "LOG": str(log)},
                                    text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("health=UNHEALTHY", result.stdout)
            self.assertIn("auth-fail=1", result.stdout)

    def test_status_reports_the_running_launch_write_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "FKST_GITHUB_WRITE=1 FKST_GITHUB_WRITER_LOGIN=resolved-bot FKST_GITHUB_CLAIM_MODE=label "
                "FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=0\nlast event\n",
                encoding="ascii",
            )
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^status_one()/,/^}}/p' "{OPERATOR}")"
cfg() {{ HOST=/host; PKGSRC=/platform; REPO=example/repo; }}
pidof_df() {{ echo 123; }}
latest_log() {{ echo "$LOG"; }}
fmt_uptime() {{ echo 1m00s; }}
engine_panic_count() {{ echo 0; }}
ps() {{ echo 00:01:00; }}
git() {{ echo abcdef123456; }}
status_one fixture
'''
            result = subprocess.run(
                ["bash", "-c", command], env={**os.environ, "LOG": str(log)},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("write=1", result.stdout)
            self.assertIn("writer=resolved-bot", result.stdout)
            self.assertIn("claim=label", result.stdout)
            self.assertIn("label-exclusive=0", result.stdout)

    def test_platform_source_role_is_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "target-owned-name"\ngit = "ssh://git@example.com/team/platform"\npackages = ["one", "two"]\n',
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
                    "https://example.com/team/platform.git",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "one two")

    def test_platform_source_url_zero_matches_fails_closed_with_observed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "other"\ngit = "https://example.com/team/other.git"\npackages = ["one"]\n',
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
                    "https://example.com/team/platform.git",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("https://example.com/team/platform.git", result.stdout)
            self.assertIn("no matches", result.stdout)
            self.assertIn("other", result.stdout)

    def test_platform_source_url_two_matches_fails_closed_with_observed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "first"\ngit = "https://example.com/team/platform.git"\npackages = ["one"]\n'
                '[[external_sources]]\nid = "second"\ngit = "git://example.com/team/platform"\npackages = ["two"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                ["python3", str(MANIFEST), "platform-packages", "deployment-a", str(host), str(platform),
                 "ssh://git@example.com/team/platform.git"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("ssh://git@example.com/team/platform.git", result.stdout)
            self.assertIn("2 matches", result.stdout)
            self.assertIn("first", result.stdout)
            self.assertIn("second", result.stdout)

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
