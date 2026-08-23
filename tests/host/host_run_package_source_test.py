#!/usr/bin/env python3
"""The launch contract accepts package sources beyond the platform.

`--platform-packages` names packages beneath `--platform-root`. `--package-source` carries a
binding the declaration already made: this root supplies these names. Nothing here re-derives
composition from the target, so a package cannot resolve to the wrong source at launch.
"""

from __future__ import annotations

import textwrap
import unittest

from host_run_fixture import HostRunHarness, create_git_source, shell_quote


def parse_supervise(harness: HostRunHarness, command_args: list[str]):
    quoted = " ".join(shell_quote(arg) for arg in command_args)
    return harness.run_helper(
        textwrap.dedent(
            f"""\
            set -euo pipefail
            source host/host_run.sh
            host_run_parse_supervise_args {quoted}
            """
        )
    )


class HostRunPackageSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.h = HostRunHarness()
        self.addCleanup(self.h.close)
        extra, _ = create_git_source(
            self.h.root,
            "extra-packages",
            {
                "packages/site-board/fkst.toml": 'kind = "package"\nname = "site-board"\n',
                "packages/site-radar/fkst.toml": 'kind = "package"\nname = "site-radar"\n',
                "packages/github-proxy/fkst.toml": 'kind = "package"\nname = "github-proxy"\n',
            },
        )
        # The contract normalises every root it is given; compare against the same form.
        self.extra = extra.resolve()

    def base_args(self) -> list[str]:
        return [
            "--project-root", str(self.h.substrate_host),
            "--platform-root", str(self.h.platform),
            "--platform-packages", "github-proxy consensus",
            "--durable-root", str(self.h.durable),
            "--expected-engine-revision", "a" * 40,
        ]

    @staticmethod
    def source_args(root: object, names: str) -> list[str]:
        return ["--package-source", str(root), names]

    def test_a_package_source_contributes_its_roots(self) -> None:
        result = self.h.package_roots(
            self.base_args() + self.source_args(self.extra, "site-board site-radar")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"{self.h.platform.resolve()}/packages/github-proxy",
                f"{self.h.platform.resolve()}/packages/consensus",
                f"{self.extra}/packages/site-board",
                f"{self.extra}/packages/site-radar",
            ],
        )

    def test_two_package_sources_each_contribute_their_own(self) -> None:
        second, _ = create_git_source(
            self.h.root,
            "second-packages",
            {"packages/site-clock/fkst.toml": 'kind = "package"\nname = "site-clock"\n'},
        )
        second = second.resolve()
        result = self.h.package_roots(
            self.base_args()
            + self.source_args(self.extra, "site-board")
            + self.source_args(second, "site-clock")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        roots = result.stdout.split()
        self.assertIn(f"{self.extra}/packages/site-board", roots)
        self.assertIn(f"{second}/packages/site-clock", roots)

    def test_no_package_source_leaves_the_roots_unchanged(self) -> None:
        with_source = self.h.package_roots(
            self.base_args() + self.source_args(self.extra, "site-board")
        )
        without = self.h.package_roots(self.base_args())
        self.assertEqual(without.returncode, 0, without.stderr)
        self.assertNotIn("site-board", without.stdout)
        self.assertEqual(
            without.stdout.split(),
            [root for root in with_source.stdout.split() if "extra-packages" not in root],
        )

    def test_a_package_missing_from_its_source_fails_closed(self) -> None:
        result = self.h.package_roots(
            self.base_args() + self.source_args(self.extra, "absent-package")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing package source package 'absent-package'", result.stderr)

    def test_a_relative_root_is_refused(self) -> None:
        # The root is a path the caller resolved; accepting a relative one would make the
        # resolution depend on the launcher's working directory.
        result = parse_supervise(
            self.h, self.base_args() + self.source_args("extra", "site-board")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root must be absolute", result.stderr)

    def test_a_source_without_names_is_refused(self) -> None:
        result = parse_supervise(self.h, self.base_args() + self.source_args(self.extra, ""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names must not be empty", result.stderr)

    def test_a_missing_value_is_refused(self) -> None:
        result = parse_supervise(self.h, self.base_args() + ["--package-source"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--package-source requires", result.stderr)

    def test_a_source_root_containing_equals_is_not_split(self) -> None:
        source, _ = create_git_source(
            self.h.root,
            "extra=packages",
            {"packages/site-clock/fkst.toml": 'kind = "package"\nname = "site-clock"\n'},
        )
        result = self.h.package_roots(
            self.base_args() + self.source_args(source.resolve(), "site-clock")
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            f"{source.resolve()}/packages/site-clock", result.stdout.splitlines()
        )

    def test_a_package_source_cannot_repeat_a_platform_name(self) -> None:
        result = self.h.package_roots(
            self.base_args() + self.source_args(self.extra, "github-proxy")
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate package name 'github-proxy'", result.stderr)

    def test_a_package_source_name_cannot_escape_its_launch_root(self) -> None:
        escaped_target = (self.extra / "packages" / "../../outside").resolve()
        escaped_target.mkdir()
        result = self.h.package_roots(
            self.base_args() + self.source_args(self.extra, "../../outside")
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid package name '../../outside'", result.stderr)


if __name__ == "__main__":
    unittest.main()
