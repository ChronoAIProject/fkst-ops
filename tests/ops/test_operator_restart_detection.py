#!/usr/bin/env python3
"""Checks for operator restart and staleness detection."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"


class OperatorRestartDetectionTest(unittest.TestCase):
    def _classify_environment(
        self, running_sha256: str | None, desired_sha256: str,
        *, running_generation: str = "generation-old",
        desired_generation: str = "generation-current",
    ) -> str:
        command = f'''eval "$(sed -n '/^platform_paths_require_restart()/,/^}}/p' "{OPERATOR}")"
eval "$(sed -n '/^_proc_stale()/,/^}}/p' "{OPERATOR}")"
cfg() {{ PKGSRC=/platform; INTEGRATION_BRANCH=dev; PYTHON=python3; PLATFORM_SOURCE_PIN=; return 0; }}
pidof_df() {{ echo 123; }}
latest_log() {{ echo "$TEST_LOG"; }}
derive_devloop_pkgs_from_workspace() {{ DEVLOOP_PKGS=platform-package; }}
resolve_engine_pair() {{ ENGINE_REVISION=aaaaaaaa; }}
resolve_deployment_child_environment() {{ :; }}
deployment_child_environment_sha256() {{ echo "$DESIRED_ENVIRONMENT_SHA256"; }}
git() {{
  case "$*" in
    *" fetch "*) return 0 ;;
    *" rev-parse "*) echo 11111111; return 0 ;;
  esac
  return 1
}}
DESIRED_ENVIRONMENT_SHA256="$1"
DESIRED_GENERATION="$2"
TEST_LOG="$3"
_proc_stale deployment
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "supervise.log"
            marker = (
                f" LAUNCH_ENV_SHA256={running_sha256}" if running_sha256 is not None else ""
            )
            log.write_text(
                "EVENT=code_provenance platform-package@11111111 "
                f"ENGINE_VER=aaaaaaaa{marker}\n"
                f"CONTROL_GENERATION={running_generation}\n",
                encoding="ascii",
            )
            result = subprocess.run(
                [
                    "bash", "-c", command, "test", desired_sha256,
                    desired_generation, str(log),
                ],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _platform_paths_require_restart(self, *paths: str) -> bool:
        command = f'''eval "$(sed -n '/^platform_paths_require_restart()/,/^}}/p' "{OPERATOR}")"
printf '%s\\n' "$@" | platform_paths_require_restart
'''
        result = subprocess.run(
            ["bash", "-c", command, "test", *paths],
            text=True, capture_output=True, check=False,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode == 0

    def test_platform_scripts_change_requires_restart(self) -> None:
        for path in ("scripts/run_bin.sh", "scripts/run.sh", "scripts/host_run.sh"):
            with self.subTest(path=path):
                self.assertTrue(self._platform_paths_require_restart(path))

    def test_platform_ci_test_and_checker_changes_do_not_require_restart(self) -> None:
        for path in (
            ".github/workflows/ci.yml",
            "scripts/ci_workflow_test.py",
            "scripts/git_compat_test.sh",
            "scripts/check_repo.py",
            "scripts/check_repo_library_layering.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(self._platform_paths_require_restart(path))

    def test_platform_migration_and_launch_config_changes_require_restart(self) -> None:
        for path in ("migration/catalog.json", "fkst.workspace.toml", "fkst.lock"):
            with self.subTest(path=path):
                self.assertTrue(self._platform_paths_require_restart(path))

    def test_platform_packages_change_still_requires_restart(self) -> None:
        self.assertTrue(
            self._platform_paths_require_restart("packages/github-devloop/core.lua")
        )

    def test_platform_library_change_still_requires_restart(self) -> None:
        self.assertTrue(self._platform_paths_require_restart("libraries/devloop/base.lua"))

    def test_proc_stale_fails_closed_when_git_diff_fails(self) -> None:
        command = f'''eval "$(sed -n '/^platform_paths_require_restart()/,/^}}/p' "{OPERATOR}")"
eval "$(sed -n '/^_proc_stale()/,/^}}/p' "{OPERATOR}")"
cfg() {{ PKGSRC=/platform; INTEGRATION_BRANCH=dev; PYTHON=python3; return 0; }}
pidof_df() {{ echo 123; }}
latest_log() {{ echo "$TEST_LOG"; }}
derive_devloop_pkgs_from_workspace() {{ DEVLOOP_PKGS=github-devloop; }}
resolve_engine_pair() {{ ENGINE_REVISION=aaaaaaaa; }}
git() {{
  case "$*" in
    *" fetch "*) return 0 ;;
    *" rev-parse "*) echo bbbbbbbb; return 0 ;;
    *" diff --name-only "*) return 1 ;;
  esac
  return 1
}}
TEST_LOG="$1"
_proc_stale "$2"
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            log = Path(temp_dir) / "supervise.log"
            log.write_text(
                "EVENT=code_provenance github-devloop@11111111 ENGINE_VER=aaaaaaaa\n",
                encoding="ascii",
            )
            result = subprocess.run(
                ["bash", "-c", command, "test", str(log), "packages"],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "pkg-stale")

    def test_changed_launch_environment_is_stale(self) -> None:
        self.assertEqual(
            self._classify_environment("1" * 64, "2" * 64),
            "environment-stale",
        )

    def test_matching_launch_environment_is_current(self) -> None:
        self.assertEqual(self._classify_environment("1" * 64, "1" * 64), "current")

    def test_launch_without_environment_identity_is_stale(self) -> None:
        self.assertEqual(
            self._classify_environment(None, "1" * 64),
            "environment-stale",
        )

    def test_generation_change_without_environment_change_is_current(self) -> None:
        self.assertEqual(
            self._classify_environment(
                "1" * 64, "1" * 64,
                running_generation="generation-superseded",
                desired_generation="generation-current",
            ),
            "current",
        )

    def test_sync_restarts_environment_stale_supervise(self) -> None:
        command = f'''eval "$(sed -n '/^cmd_sync()/,/^}}/p' "{OPERATOR}")"
expand() {{ echo deployment; }}
cfg() {{
  HOST=/platform; PKGSRC=/platform; RUNTIME_ROOT=/runtime
  UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
  PLATFORM_SOURCE_PIN=; TARGET_SOURCE_PIN=
}}
git_lock_sweep() {{ :; }}
derive_devloop_pkgs_from_workspace() {{ :; }}
ensure_integration_caught_up() {{ :; }}
sync_to_run_branch() {{ :; }}
bin_ensure_fresh() {{ echo current; }}
_proc_stale() {{ echo environment-stale; }}
restart_one() {{ echo "restarted:$1"; }}
cmd_sync all
'''
        result = subprocess.run(
            ["bash", "-c", command], text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("environment-stale -> auto-restart", result.stdout)
        self.assertIn("restarted:deployment", result.stdout)

    def test_platform_docs_only_change_does_not_require_restart(self) -> None:
        self.assertFalse(
            self._platform_paths_require_restart("docs/user/deployment.md", "README.md")
        )

    def test_platform_skill_only_change_does_not_require_restart(self) -> None:
        self.assertFalse(
            self._platform_paths_require_restart(
                ".claude/skills/dogfood-github-devloop/SKILL.md",
                ".claude/skills/dogfood-github-devloop/dogfood.sh",
            )
        )

    def test_platform_code_change_is_not_hidden_by_docs_change(self) -> None:
        self.assertTrue(
            self._platform_paths_require_restart(
                "docs/user/deployment.md", "libraries/devloop/base.lua"
            )
        )


if __name__ == "__main__":
    unittest.main()
