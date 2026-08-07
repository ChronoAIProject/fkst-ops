#!/usr/bin/env python3
"""Source identity validation tests for host/host_run.sh."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from host_run_fixture import (
    HostRunHarness,
    commit_git_file,
    create_git_source,
    run_argv,
    shell_quote,
)


SOURCE_ID = "fkst-packages-platform"


class HostRunSourceIdentityTest(unittest.TestCase):
    def assert_duplicate_error(
        self,
        result,
        *,
        first_location: str,
        second_location: str,
    ) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"duplicate source id '{SOURCE_ID}'", result.stderr)
        self.assertIn(first_location, result.stderr)
        self.assertIn(second_location, result.stderr)

    def test_duplicate_workspace_source_id_fails_closed(self) -> None:
        for variant in ("identical", "conflicting"):
            with self.subTest(variant=variant):
                h = HostRunHarness()
                try:
                    first_repo, first_rev = create_git_source(
                        h.root,
                        f"workspace-{variant}-first",
                        {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
                    )
                    second_repo = first_repo
                    second_rev = first_rev
                    if variant == "conflicting":
                        second_repo, second_rev = create_git_source(
                            h.root,
                            "workspace-conflicting-second",
                            {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
                        )
                    h.write_workspace_manifest(
                        external_sources=[
                            (SOURCE_ID, first_repo, ["github-proxy"]),
                            (SOURCE_ID, second_repo, ["github-proxy"]),
                        ]
                    )
                    h.write_external_sources_lock([(SOURCE_ID, second_repo, second_rev)])

                    result = h.package_roots(
                        [
                            "--project-root",
                            str(h.website_host),
                            "--platform-root",
                            str(second_repo),
                            "--platform-packages",
                            "github-proxy",
                            "--durable-root",
                            str(h.durable),
                            "--runtime-root",
                            str(h.runtime),
                        ]
                    )

                    self.assert_duplicate_error(
                        result,
                        first_location="fkst.workspace.toml external_sources[1]",
                        second_location="fkst.workspace.toml external_sources[2]",
                    )
                finally:
                    h.close()

    def test_bare_platform_root_fails_shape_validation_with_git_context(self) -> None:
        h = HostRunHarness()
        try:
            configured = run_argv(["git", "config", "core.bare", "true"], cwd=h.platform)
            self.assertEqual(configured.returncode, 0, configured.stderr)

            result = h.package_roots(
                [
                    "--project-root",
                    str(h.website_host),
                    "--platform-root",
                    str(h.platform),
                    "--platform-packages",
                    "github-proxy",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(h.platform), result.stderr)
            self.assertIn("git rev-parse --is-inside-work-tree=true", result.stderr)
            self.assertIn("core.bare=true", result.stderr)
        finally:
            h.close()

    def test_platform_work_tree_validation_ignores_git_stderr(self) -> None:
        h = HostRunHarness()
        try:
            args = [
                "--project-root",
                str(h.website_host),
                "--platform-root",
                str(h.platform),
                "--platform-packages",
                "github-proxy",
                "--durable-root",
                str(h.durable),
                "--runtime-root",
                str(h.runtime),
            ]
            quoted = " ".join(shell_quote(arg) for arg in args)
            with mock.patch.dict(os.environ, {"GIT_TRACE": "1"}):
                result = h.run_helper(
                    "set -euo pipefail\n"
                    "source host/host_run.sh\n"
                    f"host_run_parse_supervise_args {quoted}\n"
                    "host_run_validate_shape\n"
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("trace:", result.stderr)
        finally:
            h.close()

    def test_required_git_failure_reports_invocation_cwd_and_stderr(self) -> None:
        h = HostRunHarness()
        try:
            unborn = h.root / "unborn-platform"
            (unborn / "packages" / "github-proxy").mkdir(parents=True)
            (unborn / "packages" / "github-proxy" / "fkst.toml").write_text(
                'kind = "package"\nname = "github-proxy"\n',
                encoding="utf-8",
            )
            initialized = run_argv(["git", "init", "-q"], cwd=unborn)
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            h.write_workspace_manifest(
                external_sources=[(SOURCE_ID, unborn, ["github-proxy"])]
            )
            h.write_external_sources_lock([(SOURCE_ID, unborn, "0" * 40)])

            result = h.package_roots(
                [
                    "--project-root",
                    str(h.website_host),
                    "--platform-root",
                    str(unborn),
                    "--platform-packages",
                    "github-proxy",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("git rev-parse HEAD failed with exit 128", result.stderr)
            self.assertIn(f"cwd={unborn.resolve()}", result.stderr)
            self.assertIn("fatal:", result.stderr)
        finally:
            h.close()

    def test_duplicate_lock_source_id_fails_closed(self) -> None:
        for variant in ("identical", "conflicting"):
            with self.subTest(variant=variant):
                h = HostRunHarness()
                try:
                    platform_repo, first_rev = create_git_source(
                        h.root,
                        f"lock-{variant}-source",
                        {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
                    )
                    second_rev = first_rev
                    if variant == "conflicting":
                        second_rev = commit_git_file(platform_repo, "second.txt", "second revision\n")
                    h.write_workspace_manifest(
                        external_sources=[(SOURCE_ID, platform_repo, ["github-proxy"])]
                    )
                    h.write_external_sources_lock(
                        [
                            (SOURCE_ID, platform_repo, first_rev),
                            (SOURCE_ID, platform_repo, second_rev),
                        ]
                    )

                    result = h.package_roots(
                        [
                            "--project-root",
                            str(h.website_host),
                            "--platform-root",
                            str(platform_repo),
                            "--platform-packages",
                            "github-proxy",
                            "--durable-root",
                            str(h.durable),
                            "--runtime-root",
                            str(h.runtime),
                        ]
                    )

                    self.assert_duplicate_error(
                        result,
                        first_location="fkst.lock external_source[1]",
                        second_location="fkst.lock external_source[2]",
                    )
                finally:
                    h.close()


if __name__ == "__main__":
    unittest.main()
