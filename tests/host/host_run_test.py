#!/usr/bin/env python3
"""Behavior tests for host/host_run.sh."""

from __future__ import annotations

import json
import os
import textwrap
import unittest
from pathlib import Path

from host_run_fixture import (
    HostRunHarness,
    commit_git_file,
    create_git_source,
    run_argv,
    shell_quote,
)


HOST_FIXTURE_ROOT_ENV = "FKST_HOST_FIXTURE_ROOT"
HOST_FIXTURE_ROOT = Path(value) if (value := os.environ.get(HOST_FIXTURE_ROOT_ENV)) else None
REQUIRES_HOST_FIXTURE = unittest.skipUnless(
    HOST_FIXTURE_ROOT is not None and HOST_FIXTURE_ROOT.is_dir(),
    f"set {HOST_FIXTURE_ROOT_ENV} to the host fixture checkout",
)


def host_fixture_path(*parts: str) -> Path:
    assert HOST_FIXTURE_ROOT is not None
    return HOST_FIXTURE_ROOT.joinpath(*parts)


class HostRunTest(unittest.TestCase):
    def test_packages_host_uses_project_packages_for_host_packages(self) -> None:
        h = HostRunHarness()
        try:
            h.write_workspace_manifest(root=h.packages_host, workspace_units=["packages/*"])
            result = h.package_roots(
                [
                    "--project-root",
                    str(h.packages_host),
                    "--platform-root",
                    str(h.packages_host),
                    "--platform-packages",
                    "github-proxy consensus",
                    "--host-packages",
                    "autochrono",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str((h.packages_host / "packages" / "github-proxy").resolve()),
                    str((h.packages_host / "packages" / "consensus").resolve()),
                    str(h.packages_host / "packages" / "autochrono"),
                ],
            )
        finally:
            h.close()

    def test_workspace_platform_packages_resolve_from_workspace_units(self) -> None:
        h = HostRunHarness()
        try:
            h.write_workspace_manifest(root=h.packages_host, workspace_units=["packages/*"])
            result = h.package_roots(
                [
                    "--project-root",
                    str(h.packages_host),
                    "--platform-root",
                    str(h.packages_host),
                    "--platform-packages",
                    "github-proxy consensus",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str((h.packages_host / "packages" / "github-proxy").resolve()),
                    str((h.packages_host / "packages" / "consensus").resolve()),
                ],
            )
        finally:
            h.close()

    def test_substrate_host_has_only_platform_packages(self) -> None:
        h = HostRunHarness()
        try:
            platform_repo, platform_rev = create_git_source(
                h.root,
                "substrate-platform-source",
                {
                    "packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n',
                    "packages/consensus/fkst.toml": 'kind = "package"\nname = "consensus"\n',
                },
            )
            h.write_workspace_manifest(
                root=h.substrate_host,
                external_sources=[("fkst-packages-platform", platform_repo, ["github-proxy", "consensus"])],
            )
            h.write_external_sources_lock([("fkst-packages-platform", platform_repo, platform_rev)], root=h.substrate_host)
            result = h.package_roots(
                [
                    "--project-root",
                    str(h.substrate_host),
                    "--platform-root",
                    str(platform_repo),
                    "--platform-packages",
                    "github-proxy consensus",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str((platform_repo / "packages" / "github-proxy").resolve()),
                    str((platform_repo / "packages" / "consensus").resolve()),
                ],
            )
        finally:
            h.close()

    def test_website_host_uses_fkst_local_packages_for_host_packages(self) -> None:
        h = HostRunHarness()
        try:
            platform_repo, platform_rev = create_git_source(
                h.root,
                "website-platform-source",
                {
                    "packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n',
                    "packages/consensus/fkst.toml": 'kind = "package"\nname = "consensus"\n',
                },
            )
            h.write_workspace_manifest(
                external_sources=[("fkst-packages-platform", platform_repo, ["github-proxy", "consensus"])],
            )
            h.write_external_sources_lock([("fkst-packages-platform", platform_repo, platform_rev)])
            result = h.package_roots(
                [
                    "--project-root",
                    str(h.website_host),
                    "--platform-root",
                    str(platform_repo),
                    "--platform-packages",
                    "github-proxy consensus",
                    "--host-packages",
                    "site-board",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str((platform_repo / "packages" / "github-proxy").resolve()),
                    str((platform_repo / "packages" / "consensus").resolve()),
                    str(h.website_host / ".fkst" / "local-packages" / "site-board"),
                ],
            )
        finally:
            h.close()

    def test_explicit_local_packages_root_overrides_host_default(self) -> None:
        h = HostRunHarness()
        custom_local = h.root / "custom-local-packages"
        try:
            platform_repo, platform_rev = create_git_source(
                h.root,
                "custom-local-platform-source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            h.write_workspace_manifest(external_sources=[("fkst-packages-platform", platform_repo, ["github-proxy"])])
            h.write_external_sources_lock([("fkst-packages-platform", platform_repo, platform_rev)])
            (custom_local / "site-board").mkdir(parents=True)
            result = h.package_roots(
                [
                    "--project-root",
                    str(h.website_host),
                    "--platform-root",
                    str(platform_repo),
                    "--local-packages",
                    str(custom_local),
                    "--platform-packages",
                    "github-proxy",
                    "--host-packages",
                    "site-board",
                    "--durable-root",
                    str(h.durable),
                    "--runtime-root",
                    str(h.runtime),
                ]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str((platform_repo / "packages" / "github-proxy").resolve()),
                    str(custom_local / "site-board"),
                ],
            )
        finally:
            h.close()

    def test_host_supervise_requires_target_workspace_manifest(self) -> None:
        h = HostRunHarness()
        try:
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(h.platform)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target fkst.workspace.toml is required for host supervise", result.stderr)
        finally:
            h.close()

    def test_platform_package_roots_are_resolved_from_target_workspace_external_source(self) -> None:
        h = HostRunHarness()
        try:
            platform_repo, platform_rev = create_git_source(
                h.root,
                "platform-source",
                {
                    "packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n',
                    "packages/consensus/fkst.toml": 'kind = "package"\nname = "consensus"\n',
                },
            )
            tools_repo, tools_rev = create_git_source(
                h.root,
                "tools-source",
                {"tools/probe.txt": "tool source\n"},
            )
            h.write_workspace_manifest(
                external_sources=[
                    ("fkst-packages-platform", platform_repo, ["github-proxy"]),
                    ("site-tools", tools_repo, ["site-tools"]),
                ]
            )
            h.write_external_sources_lock(
                [
                    ("fkst-packages-platform", platform_repo, platform_rev),
                    ("site-tools", tools_repo, tools_rev),
                ]
            )
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(platform_repo)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    printf 'platform=%s\\n' "$HOST_RUN_PLATFORM_ROOT"
                    host_run_print_package_roots
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"platform={platform_repo.resolve()}",
                    str((platform_repo / "packages" / "github-proxy").resolve()),
                ],
            )
            self.assertTrue((platform_repo / "packages" / "github-proxy").is_dir())
            self.assertFalse((h.website_host / ".fkst" / "run" / "site-tools").exists())
        finally:
            h.close()

    def test_external_platform_source_must_match_trusted_platform_root(self) -> None:
        h = HostRunHarness()
        try:
            trusted_repo, _trusted_rev = create_git_source(
                h.root,
                "trusted-platform-source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            attacker_repo, attacker_rev = create_git_source(
                h.root,
                "attacker-platform-source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            h.write_workspace_manifest(
                external_sources=[("fkst-packages-platform", attacker_repo, ["github-proxy"])],
            )
            h.write_external_sources_lock([("fkst-packages-platform", attacker_repo, attacker_rev)])
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(trusted_repo)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match trusted --platform-root", result.stderr)
            self.assertFalse((h.website_host / ".fkst" / "run" / "fkst-packages-platform").exists())
        finally:
            h.close()

    def test_workspace_platform_package_requires_trusted_platform_root(self) -> None:
        h = HostRunHarness()
        try:
            trusted_repo, _trusted_rev = create_git_source(
                h.root,
                "trusted-platform-source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            (h.website_host / "packages" / "github-proxy").mkdir(parents=True)
            (h.website_host / "packages" / "github-proxy" / "fkst.toml").write_text(
                'kind = "package"\nname = "github-proxy"\n',
                encoding="utf-8",
            )
            h.write_workspace_manifest(workspace_units=["packages/*"])
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(trusted_repo)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires trusted --platform-root", result.stderr)
            self.assertNotIn(str(h.website_host / "packages" / "github-proxy"), result.stdout)
        finally:
            h.close()

    def test_ambiguous_target_workspace_platform_package_fails_closed(self) -> None:
        h = HostRunHarness()
        try:
            platform_repo, platform_rev = create_git_source(
                h.root,
                "platform-source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            h.write_workspace_manifest(
                workspace_packages=["github-proxy"],
                external_sources=[("fkst-packages-platform", platform_repo, ["github-proxy"])],
            )
            h.write_external_sources_lock([("fkst-packages-platform", platform_repo, platform_rev)])
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(h.platform)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous target fkst.workspace.toml platform package 'github-proxy'", result.stderr)
        finally:
            h.close()

    def test_host_external_source_uses_trusted_platform_root_when_head_advanced_past_lock(self) -> None:
        h = HostRunHarness()
        try:
            platform_repo, platform_rev = create_git_source(
                h.root,
                "platform-source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            tools_repo, tools_rev = create_git_source(
                h.root,
                "tools-source",
                {"tools/probe.txt": "tool source\n"},
            )
            h.write_external_sources_lock(
                [
                    ("fkst-packages-platform", platform_repo, platform_rev),
                    ("site-tools", tools_repo, tools_rev),
                ]
            )
            platform_head = commit_git_file(
                platform_repo,
                "packages/github-proxy/current.txt",
                "advanced local platform checkout\n",
            )
            self.assertNotEqual(platform_head, platform_rev)
            h.write_workspace_manifest(
                external_sources=[
                    ("fkst-packages-platform", platform_repo, ["github-proxy"]),
                    ("site-tools", tools_repo, ["site-tools"]),
                ]
            )
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(platform_repo)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    printf 'platform=%s\\n' "$HOST_RUN_PLATFORM_ROOT"
                    host_run_print_package_roots
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"platform={platform_repo.resolve()}",
                    str((platform_repo / "packages" / "github-proxy").resolve()),
                ],
            )
            self.assertTrue((platform_repo / "packages" / "github-proxy").is_dir())
            self.assertTrue((platform_repo / "packages" / "github-proxy" / "current.txt").is_file())
            self.assertFalse((h.website_host / ".fkst" / "run" / "fkst-packages-platform").exists())
            self.assertFalse((h.website_host / ".fkst" / "run" / "site-tools").exists())
        finally:
            h.close()

    def test_host_external_source_ignores_wrong_existing_hydrated_checkout(self) -> None:
        h = HostRunHarness()
        try:
            source_repo, source_rev = create_git_source(
                h.root,
                "source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            stale_repo, _stale_rev = create_git_source(
                h.root,
                "stale",
                {"stale.txt": "stale\n"},
            )
            h.write_workspace_manifest(external_sources=[("fkst-packages-platform", source_repo, ["github-proxy"])])
            h.write_external_sources_lock([("fkst-packages-platform", source_repo, source_rev)])
            checkout = h.website_host / ".fkst" / "run" / "fkst-packages-platform"
            checkout.parent.mkdir(parents=True, exist_ok=True)
            result = run_argv(["git", "clone", "-q", str(stale_repo), str(checkout)], cwd=h.root)
            self.assertEqual(result.returncode, 0, result.stderr)

            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(source_repo)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_build_package_roots
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            head = run_argv(["git", "rev-parse", "HEAD"], cwd=checkout)
            self.assertEqual(head.returncode, 0, head.stderr)
            self.assertNotEqual(head.stdout.strip(), source_rev)
            origin = run_argv(["git", "config", "--get", "remote.origin.url"], cwd=checkout)
            self.assertEqual(origin.returncode, 0, origin.stderr)
            self.assertEqual(origin.stdout.strip(), str(stale_repo))
        finally:
            h.close()

    def test_external_source_lock_is_required_and_identity_checked(self) -> None:
        cases = ("missing", "mismatched_git", "invalid_rev")
        for case in cases:
            with self.subTest(case=case):
                h = HostRunHarness()
                try:
                    platform_repo, platform_rev = create_git_source(
                        h.root,
                        f"{case}-platform-source",
                        {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
                    )
                    h.write_workspace_manifest(
                        external_sources=[("fkst-packages-platform", platform_repo, ["github-proxy"])]
                    )
                    expected = "target fkst.lock is required for external platform packages"
                    if case == "mismatched_git":
                        other_repo, other_rev = create_git_source(h.root, "other-source", {"README.md": "other\n"})
                        h.write_external_sources_lock([("fkst-packages-platform", other_repo, other_rev)])
                        expected = "fkst.workspace.toml external_sources(id=fkst-packages-platform) git does not match fkst.lock"
                    elif case == "invalid_rev":
                        h.write_external_sources_lock([("fkst-packages-platform", platform_repo, "not-a-sha")])
                        expected = "fkst.lock external_source(id=fkst-packages-platform) is missing resolved.rev as a full git SHA"
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
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                finally:
                    h.close()

    @REQUIRES_HOST_FIXTURE
    def test_supervise_contract_uses_trusted_platform_root_for_launch_args(self) -> None:
        h = HostRunHarness()
        capture = h.root / "capture.json"
        try:
            substrate_repo, _ = create_git_source(
                h.root,
                "fkst-substrate",
                {"Cargo.toml": "[workspace]\n"},
            )
            fake_bin = substrate_repo / "target" / "debug" / "fkst-framework"
            fake_bin.parent.mkdir(parents=True)
            source_repo, source_rev = create_git_source(
                h.root,
                "source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            h.write_workspace_manifest(external_sources=[("fkst-packages-platform", source_repo, ["github-proxy"])])
            h.write_external_sources_lock([("fkst-packages-platform", source_repo, source_rev)])
            source_head = commit_git_file(
                source_repo,
                "packages/github-proxy/current.txt",
                "advanced local platform checkout\n",
            )
            self.assertNotEqual(source_head, source_rev)
            fake_bin.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    import json
                    import os
                    import pathlib
                    import subprocess
                    import sys

                    checkout = pathlib.Path({json.dumps(str(source_repo))})
                    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
                    pathlib.Path({json.dumps(str(capture))}).write_text(json.dumps({{"argv": sys.argv, "head": head, "runtime": os.environ.get("FKST_RUNTIME_ROOT"), "durable": os.environ.get("FKST_DURABLE_ROOT"), "project_root": os.environ.get("FKST_PROJECT_ROOT"), "repository_roots": os.environ.get("FKST_CODEX_REPOSITORY_ROOTS")}}, sort_keys=True) + "\\n", encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            fake_bin.chmod(0o755)
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    source {shell_quote(host_fixture_path("scripts", "run_bin.sh"))}
                    BIN={shell_quote(fake_bin)}
                    export CI=1
                    ensure_fresh_bin
                    export FKST_PROJECT_ROOT=/untrusted/launch-directory
                    export FKST_CODEX_REPOSITORY_ROOTS=/untrusted/ambient-repository
                    host_run_supervise_contract --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(source_repo)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(payload["head"], source_head)
            self.assertEqual(
                payload["argv"],
                [
                    str(fake_bin),
                    "supervise",
                    "--project-root",
                    str(h.website_host),
                    "--package-root",
                    str((source_repo / "packages" / "github-proxy").resolve()),
                    "--framework-bin",
                    str(fake_bin),
                ],
            )
            self.assertEqual(payload["runtime"], str(h.runtime))
            self.assertEqual(payload["durable"], str(h.durable))
            self.assertEqual(Path(payload["project_root"]).resolve(), h.website_host.resolve())
            self.assertEqual(
                payload["repository_roots"].splitlines(),
                [
                    str(h.website_host.resolve()),
                    str(source_repo.resolve()),
                    str(substrate_repo.resolve()),
                ],
            )
        finally:
            h.close()

    @REQUIRES_HOST_FIXTURE
    def test_package_local_supervise_carries_traceable_framework_checkout(self) -> None:
        h = HostRunHarness()
        capture = h.root / "package-local-capture.json"
        try:
            package_repo = h.root / "package-repository"
            package_root = package_repo / "packages" / "example"
            package_root.mkdir(parents=True)
            (package_root / "fkst.toml").write_text(
                'kind = "package"\nname = "example"\n',
                encoding="utf-8",
            )
            substrate_repo, _ = create_git_source(
                h.root,
                "package-local-fkst-substrate",
                {"Cargo.toml": "[workspace]\n"},
            )
            fake_bin = substrate_repo / "target" / "debug" / "fkst-framework"
            fake_bin.parent.mkdir(parents=True)
            fake_bin.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    import json
                    import os
                    import pathlib
                    import sys

                    pathlib.Path({json.dumps(str(capture))}).write_text(json.dumps({{"argv": sys.argv, "repository_roots": os.environ.get("FKST_CODEX_REPOSITORY_ROOTS")}}, sort_keys=True) + "\\n", encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            fake_bin.chmod(0o755)

            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source {shell_quote(host_fixture_path("scripts", "run.sh"))}
                    ROOT={shell_quote(package_repo)}
                    FKST_DIR="$ROOT/.fkst"
                    SOURCE_PACKAGES_ROOT="$ROOT/packages"
                    LOCAL_PACKAGES_ROOT="$FKST_DIR/local-packages"
                    EXTERNAL_PACKAGES_ROOT="$FKST_DIR/packages"
                    DEFAULT_RUNTIME_ROOT="$FKST_DIR/run/runtime"
                    DEFAULT_DURABLE_ROOT="$FKST_DIR/run/durable"
                    BIN={shell_quote(fake_bin)}
                    export BIN CI=1 FKST_RATE_POOL_ROOT={shell_quote(str(h.root / "rate-pool"))}
                    unset FKST_PROJECT_ROOT FKST_RUNTIME_ROOT FKST_DURABLE_ROOT FKST_CODEX_REPOSITORY_ROOTS
                    ensure_fresh_bin
                    cmd_supervise_old example
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["repository_roots"].splitlines(),
                [str(package_repo.resolve()), str(substrate_repo.resolve())],
            )
        finally:
            h.close()

    def test_supervise_fails_closed_when_target_workspace_does_not_declare_platform_packages(self) -> None:
        h = HostRunHarness()
        try:
            source_repo, source_rev = create_git_source(
                h.root,
                "source",
                {"packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n'},
            )
            h.write_external_sources_lock([("fkst-packages-platform", source_repo, source_rev)])
            (h.website_host / "fkst.workspace.toml").write_text(
                textwrap.dedent(
                    f"""\
                    [workspace]
                    units = []

                    [[external_sources]]
                    id = "fkst-packages-platform"
                    git = {json.dumps(str(source_repo))}
                    rev = {json.dumps(source_rev)}
                    libraries = ["contract"]
                    """
                ),
                encoding="utf-8",
            )
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args --project-root {shell_quote(h.website_host)} --platform-root {shell_quote(h.platform)} --platform-packages 'github-proxy' --durable-root {shell_quote(h.durable)} --runtime-root {shell_quote(h.runtime)}
                    host_run_validate_shape
                    host_run_build_package_roots
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "target fkst.workspace.toml does not declare platform package 'github-proxy'",
                result.stderr,
            )
        finally:
            h.close()

    def test_missing_durable_root_fails_closed(self) -> None:
        h = HostRunHarness()
        try:
            result = h.package_roots(
                [
                    "--project-root",
                    str(h.substrate_host),
                    "--platform-root",
                    str(h.platform),
                    "--platform-packages",
                    "github-proxy",
                    "--runtime-root",
                    str(h.runtime),
                ]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--durable-root is required", result.stderr)
        finally:
            h.close()

    def test_explicit_runtime_root_is_used_exactly_for_launch(self) -> None:
        h = HostRunHarness()
        try:
            args = (
                f"--project-root {shell_quote(h.substrate_host)} "
                f"--platform-root {shell_quote(h.platform)} "
                f"--platform-packages 'github-proxy' "
                f"--durable-root {shell_quote(h.durable)} "
                f"--runtime-root {shell_quote(h.runtime)}"
            )
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/host_run.sh
                    host_run_parse_supervise_args {args}
                    host_run_validate_shape
                    first="$HOST_RUN_RUNTIME_ROOT"
                    [ "$first" = {shell_quote(h.runtime)} ]
                    [ -d "$first" ]
                    host_run_parse_supervise_args {args}
                    host_run_validate_shape
                    second="$HOST_RUN_RUNTIME_ROOT"
                    [ "$second" = {shell_quote(h.runtime)} ]
                    printf '%s\\n%s\\n' "$first" "$second"
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            first, second = result.stdout.splitlines()
            self.assertEqual(first, str(h.runtime))
            self.assertEqual(second, str(h.runtime))
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
