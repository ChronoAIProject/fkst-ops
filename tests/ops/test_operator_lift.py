#!/usr/bin/env python3
"""Focused checks for the declaration-backed operator lift."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "dogfood.sh"
MANIFEST = ROOT / "ops" / "workspace_manifest.py"


class OperatorLiftTest(unittest.TestCase):
    def test_sync_never_touches_hydrated_mechanism_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pinned = Path(directory) / ".fkst" / "run" / "fkst-ops" / "checkouts" / ("a" * 40)
            (pinned / "ops").mkdir(parents=True)
            head = pinned / "HEAD"
            head.write_text("a" * 40, encoding="ascii")
            command = f'''set -uo pipefail
eval "$(sed -n '/^cmd_sync()/,/^}}/p' "{OPERATOR}")"
expand() {{ printf 'deployment-a\\n'; }}
cfg() {{
  UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
  HOST=/target; PKGSRC=/platform; SUBSTRATE_SRC=/engine; BIN=/engine/bin
  ENGINE_PROVIDER=/provider; ENGINE_CONTRACT=contract; ENGINE_PROVIDER_CONFIGURATION='{{}}'
}}
derive_devloop_pkgs_from_workspace() {{ :; }}
ensure_integration_caught_up() {{ :; }}
bin_ensure_fresh() {{ echo built; }}
_proc_stale() {{ echo current; }}
restart_one() {{ :; }}
cmd_sync all
'''
            result = subprocess.run(
                ["bash", "-c", command], text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("a" * 40, head.read_text(encoding="ascii"))
            self.assertNotIn("operator checkouts", result.stdout)

    def test_engine_provider_uses_deployment_integration_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            (checkout / ".git").mkdir()
            binary = root / "engine"
            self.assertFalse(binary.exists())
            tools = root / "tools"
            tools.mkdir()
            git = tools / "git"
            git.write_text('#!/bin/sh\ncase "$1" in branch) echo build;; pull) :;; rev-parse) printf "%040d\\n" 0;; *) exit 1;; esac\n', encoding="ascii")
            git.chmod(0o755)
            build = root / "build"
            build.write_text('#!/bin/sh\nprintf "#!/bin/sh\\nexit 0\\n" > "$1"\nchmod +x "$1"\n', encoding="ascii")
            build.chmod(0o755)
            command = f'''_self_dir="{ROOT / 'ops'}"
invoke_provider() {{ python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }}
eval "$(sed -n '/^engine_build_result()/,/^}}/p' "{OPERATOR}")"
SUBSTRATE_SRC="$1"; BIN="$2"; UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=build
ENGINE_PROVIDER="{ROOT / 'providers/engine.py'}"; ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION="$3"
engine_build_result
'''
            env = os.environ.copy()
            env["PATH"] = f"{tools}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), str(binary),
                 json.dumps({"build_command": [str(build), str(binary)]})],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["binary"], str(binary))
            self.assertTrue(binary.is_file())
            self.assertTrue(os.access(binary, os.X_OK))

    def test_engine_provider_failure_is_visible_through_dogfood_caller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = Path(directory) / "provider"
            provider.write_text(
                "#!/bin/sh\n"
                "cat >/dev/null\n"
                "printf '%s\\n' '{\"version\":\"fkst.ops.invocation.v1\",\"ok\":false,\"failure\":{\"code\":\"WRONG_BRANCH\",\"message\":\"expected branch integration, found dev\",\"details\":{}}}'\n"
                "exit 1\n",
                encoding="ascii",
            )
            provider.chmod(0o755)
            command = f'''_self_dir="{ROOT / 'ops'}"
invoke_provider() {{ python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }}
eval "$(sed -n '/^engine_build_result()/,/^}}/p' "{OPERATOR}")"
SUBSTRATE_SRC=/engine; BIN=/engine/bin; UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
ENGINE_PROVIDER="$1"; ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION='{{"build_command":["true"]}}'
bin_ensure_fresh() {{ local response; response=$(engine_build_result) || return $?; }}
bin_ensure_fresh
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(provider)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("fkst.ops.engine.v1", result.stderr)
            self.assertIn("WRONG_BRANCH", result.stderr)
            self.assertIn("expected branch integration, found dev", result.stderr)

    def test_shell_is_valid_and_uses_schema_validator(self) -> None:
        subprocess.run(["bash", "-n", str(OPERATOR)], check=True)
        source = OPERATOR.read_text(encoding="utf-8")
        self.assertIn("python3 -m schema.validator", source)
        self.assertIn('rt="$RUNTIME_ROOT/${name}.${ts}"', source)
        self.assertNotIn("GH_ORG=", source)
        self.assertNotIn("GITHUB_PROXY_POLL_LABEL_PREFIX=", source)
        self.assertNotIn("  doctor)", source)
        self.assertGreaterEqual(source.count("require_engine_binary || return 1"), 3)
        self.assertIn('require_engine_binary || { rm -rf "$tmp"; failed=1; continue; }\n    python3 "$_repo_root/board/board.py"', source)
        self.assertIn('require_engine_binary || return 1\n  BIN="$BIN" FKST_GITHUB_REPO=', source)

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
