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
