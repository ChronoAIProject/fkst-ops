#!/usr/bin/env python3
"""Behavior tests for the declaration-owned host launch contract."""

from __future__ import annotations

import textwrap
import unittest

from host_run_fixture import HostRunHarness, shell_quote


class HostRunTest(unittest.TestCase):
    def test_platform_packages_resolve_directly_from_the_platform_root(self) -> None:
        h = HostRunHarness()
        try:
            # The target may carry engine metadata of its own. The launch contract must not
            # parse it or use it to select deployment composition.
            (h.substrate_host / "fkst.workspace.toml").write_text(
                "this is deliberately not valid TOML\n", encoding="utf-8"
            )
            (h.substrate_host / "fkst.lock").write_text(
                "this is deliberately not valid TOML\n", encoding="utf-8"
            )
            result = h.package_roots(
                [
                    "--project-root", str(h.substrate_host),
                    "--platform-root", str(h.platform),
                    "--platform-packages", "github-proxy consensus",
                    "--durable-root", str(h.durable),
                    "--runtime-root", str(h.runtime),
                ]
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(h.platform.resolve() / "packages" / "github-proxy"),
                    str(h.platform.resolve() / "packages" / "consensus"),
                ],
            )
        finally:
            h.close()

    def test_retired_package_options_are_unknown(self) -> None:
        h = HostRunHarness()
        try:
            base = (
                f"--project-root {shell_quote(h.substrate_host)} "
                f"--platform-root {shell_quote(h.platform)} "
                "--platform-packages github-proxy "
                f"--durable-root {shell_quote(h.durable)}"
            )
            removed_options = (
                ("--host-packages", "site-board"),
                ("--local-packages", "/tmp/packages"),
            )
            for option, value in removed_options:
                with self.subTest(option=option):
                    result = h.run_helper(
                        textwrap.dedent(
                            f"""\
                            source host/host_run.sh
                            host_run_parse_supervise_args {base} {option} {shell_quote(value)}
                            """
                        )
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(f"unknown supervise option: {option}", result.stderr)
        finally:
            h.close()

    def test_missing_durable_root_fails_closed(self) -> None:
        h = HostRunHarness()
        try:
            result = h.package_roots(
                [
                    "--project-root", str(h.substrate_host),
                    "--platform-root", str(h.platform),
                    "--platform-packages", "github-proxy",
                    "--runtime-root", str(h.runtime),
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
                "--platform-packages github-proxy "
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
                    host_run_parse_supervise_args {args}
                    host_run_validate_shape
                    printf '%s\\n%s\\n' "$first" "$HOST_RUN_RUNTIME_ROOT"
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [str(h.runtime), str(h.runtime)])
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
