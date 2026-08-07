#!/usr/bin/env python3
"""Behavior tests for scripts/run.sh host helpers."""

from __future__ import annotations

import os
import json
import sys
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_FIXTURE_ROOT = Path("/Users/auric/fkst-packages")


def shell_quote(value: str | Path) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\\''") + "'"


class HostEntryHarness:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.host = self.root / "host"
        self.platform = self.root / "platform"
        self.local_packages = self.host / ".fkst" / "local-packages"
        (self.host / ".fkst" / "compose").mkdir(parents=True)
        self.config_dir = self.host / ".fkst" / "conformance"
        self.config_dir.mkdir(parents=True)
        for package in ("github-proxy", "idle-detector"):
            self.make_package(self.platform / "packages" / package, package)
        self.make_package(self.local_packages / "site-board", "site-board")

    def close(self) -> None:
        self.tmp.cleanup()

    def make_package(self, root: Path, name: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "fkst.toml").write_text(
            f'kind = "package"\nname = "{name}"\n\n[code]\nroot = "."\n',
            encoding="utf-8",
        )
        (root / "core.lua").write_text(
            'local M = {}\nfunction M.persistence_class() return "stateless_adapter" end\nreturn M\n',
            encoding="utf-8",
        )

    def write_host_metadata(self) -> None:
        (self.host / "fkst.workspace.toml").write_text(
            '[workspace]\nmembers = [".fkst/local-packages/site-board"]\n',
            encoding="utf-8",
        )
        compose = self.host / ".fkst" / "compose"
        compose.mkdir(parents=True, exist_ok=True)
        allowlists = self.config_dir / "allowlists"
        allowlists.mkdir()
        (allowlists / "README").write_text("host allowlists fixture\n", encoding="utf-8")

    def write_platform_workspace(self, packages: list[str]) -> None:
        (self.host / "fkst.workspace.toml").write_text(
            "[workspace]\nunits = [\".fkst/local-packages/site-board\"]\n\n"
            "[[external_sources]]\n"
            'id = "fkst-packages-platform"\n'
            f"git = {json.dumps(str(self.platform))}\n"
            'rev = "0123456789abcdef0123456789abcdef01234567"\n'
            f"packages = {json.dumps(packages)}\n",
            encoding="utf-8",
        )

    def run_helper(self, body: str) -> subprocess.CompletedProcess[str]:
        body = f'source {shell_quote(HOST_FIXTURE_ROOT / "scripts" / "composed_manifest.sh")}\n' + body
        return subprocess.run(
            ["/bin/bash", "-c", body],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def source_prelude(self) -> str:
        return textwrap.dedent(
            f"""\
            set -euo pipefail
            ROOT="$PWD"
            source host/bin_bootstrap.sh
            source host/host_run.sh
            source {shell_quote(HOST_FIXTURE_ROOT / "scripts" / "composed_manifest.sh")}
            source host/host_entry.sh
            host_entry_parse --host-root {shell_quote(self.host)} --platform-root {shell_quote(self.platform)} -- check
            host_entry_build_package_roots
            """
        )


class HostEntryTest(unittest.TestCase):
    def read_engine_command_blocks(self, engine_log: Path) -> list[list[str]]:
        blocks: list[list[str]] = []
        current: list[str] | None = None
        for line in engine_log.read_text(encoding="utf-8").splitlines():
            if line == "CMD":
                current = []
            elif line == "END":
                self.assertIsNotNone(current)
                blocks.append(current or [])
                current = None
            elif current is not None:
                current.append(line)
        return blocks

    def test_test_cleanup_exit_traps_also_disarm_the_watchdog(self) -> None:
        # A bounded-exec watchdog is armed for the whole test run (in main() and cmd_host). Any inner EXIT
        # trap a test function installs OVERRIDES the armed disarm trap, so it must itself disarm — else the
        # run finishes with the watchdog still armed and its orphaned sleep fires a stale kill -9 -pgid on a
        # possibly-reused pgid (regression fixed 2026-07-22: host_entry_cmd_test's cleanup trap dropped it).
        # Enforce it for every EXIT trap that cleans a hermetic test runtime root.
        import re

        for rel in ("host/host_entry.sh",):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for body in re.findall(r"trap '([^']*)' EXIT", src):
                if re.search(r"TEST_HERMETIC_RUNTIME_ROOT|HOST_TEST_RUNTIME_ROOT", body):
                    self.assertIn(
                        "disarm_test_deadline",
                        body,
                        f"{rel}: a test-cleanup EXIT trap does not disarm the bounded-exec watchdog "
                        f"(it overrides the armed disarm trap and would leak a stale timer): {body!r}",
                    )

    def test_configured_package_roots_split_platform_and_host_names(self) -> None:
        h = HostEntryHarness()
        try:
            (h.host / ".fkst" / "compose" / "package-roots").write_text(
                "\n".join(
                    [
                        ".fkst/local-packages/site-board",
                        "fkst-packages:packages/idle-detector",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = h.run_helper(
                h.source_prelude()
                + textwrap.dedent(
                    """\
                    printf 'roots=%s\\n' "${HOST_ENTRY_PACKAGE_ROOTS[*]}"
                    printf 'platform=%s\\n' "${HOST_ENTRY_PLATFORM_PACKAGE_NAMES[*]}"
                    printf 'host=%s\\n' "${HOST_ENTRY_HOST_PACKAGE_NAMES[*]}"
                    printf 'engine=conformance --project-root %s %s\\n' "$HOST_ENTRY_HOST_ROOT" "${HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS[*]}"
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], f"roots={h.local_packages / 'site-board'} {h.platform / 'packages' / 'idle-detector'}")
            self.assertEqual(lines[1], "platform=idle-detector")
            self.assertEqual(lines[2], "host=site-board")
            self.assertEqual(
                lines[3],
                f"engine=conformance --project-root {h.host} --package-root {h.local_packages / 'site-board'} --package-root {h.platform / 'packages' / 'idle-detector'}",
            )
        finally:
            h.close()

    def test_missing_config_discovers_host_local_packages(self) -> None:
        h = HostEntryHarness()
        try:
            result = h.run_helper(
                h.source_prelude()
                + textwrap.dedent(
                    """\
                    printf 'roots=%s\\n' "${HOST_ENTRY_PACKAGE_ROOTS[*]}"
                    printf 'platform=%s\\n' "${HOST_ENTRY_PLATFORM_PACKAGE_NAMES[*]-}"
                    printf 'host=%s\\n' "${HOST_ENTRY_HOST_PACKAGE_NAMES[*]}"
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    f"roots={h.local_packages / 'site-board'}",
                    "platform=",
                    "host=site-board",
                ],
            )
        finally:
            h.close()

    def test_supervise_delegates_to_existing_host_run_contract(self) -> None:
        h = HostEntryHarness()
        durable = h.root / "durable"
        runtime = h.root / "runtime"
        try:
            (h.host / ".fkst" / "compose" / "package-roots").write_text(
                ".fkst/local-packages/site-board\nfkst-packages:packages/github-proxy\n",
                encoding="utf-8",
            )
            h.write_platform_workspace(["github-proxy"])
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/bin_bootstrap.sh
                    source host/host_run.sh
                    source host/host_entry.sh
                    ROOT="$PWD"
                    resolve_bin() {{ BIN=/tmp/fake-bin; export BIN; }}
                    ensure_fresh_bin() {{ :; }}
                    host_run_supervise_contract() {{ printf '%s\\n' "$@"; }}
                    cmd_host --host-root {shell_quote(h.host)} --platform-root {shell_quote(h.platform)} -- supervise --durable-root {shell_quote(durable)} --runtime-root {shell_quote(runtime)} --restart
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "--project-root",
                    str(h.host),
                    "--platform-root",
                    str(h.platform),
                    "--local-packages",
                    str(h.local_packages),
                    "--platform-packages",
                    "github-proxy",
                    "--host-packages",
                    "site-board",
                    "--durable-root",
                    str(durable),
                    "--runtime-root",
                    str(runtime),
                    "--restart",
                ],
            )
        finally:
            h.close()

    def test_check_success_runs_source_ratchets_and_engine_conformance(self) -> None:
        h = HostEntryHarness()
        fake_bin = h.root / "fake-framework"
        engine_argv = h.root / "engine-argv"
        check_repo_argv = h.root / "check-repo-argv"
        fake_python_dir = h.root / "fake-python"
        fake_python_dir.mkdir()
        (fake_python_dir / "python3").write_text(
            "#!/usr/bin/env bash\n"
            "case \"$1:$2\" in\n"
            "  -B:*/scripts/check_repo.py)\n"
            "  shift 2\n"
            "  printf '%s\\n' \"$@\" > "
            + shell_quote(check_repo_argv)
            + "\n"
            "  printf 'OK\\n'\n"
            "  exit 0\n"
            "  ;;\n"
            "esac\n"
            "exec "
            + shell_quote(sys.executable)
            + " \"$@\"\n",
            encoding="utf-8",
        )
        (fake_python_dir / "python3").chmod(0o755)
        fake_bin.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$@\" > " + shell_quote(engine_argv) + "\n"
            "if [ \"${1:-}\" = \"conformance\" ]; then\n"
            "  printf '%s\\n' '{\"ok\":true}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        try:
            h.write_host_metadata()
            (h.host / ".fkst" / "compose" / "package-roots").write_text(
                ".fkst/local-packages/site-board\nfkst-packages:packages/idle-detector\n",
                encoding="utf-8",
            )
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/bin_bootstrap.sh
                    source host/host_run.sh
                    source host/host_entry.sh
                    ROOT="$PWD"
                    export PATH={shell_quote(fake_python_dir)}:"$PATH"
                    resolve_bin() {{ BIN={shell_quote(fake_bin)}; export BIN; }}
                    ensure_fresh_bin() {{ :; }}
                    cmd_host --host-root {shell_quote(h.host)} --platform-root {shell_quote(h.platform)} -- check
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("=== host source ratchets ===", result.stdout)
            self.assertIn("OK", result.stdout)
            self.assertIn('{"ok":true}', result.stdout)
            self.assertEqual(
                engine_argv.read_text(encoding="utf-8").splitlines(),
                [
                    "conformance",
                    "--project-root",
                    str(h.host),
                    "--package-root",
                    str(h.local_packages / "site-board"),
                    "--package-root",
                    str(h.platform / "packages" / "idle-detector"),
                ],
            )
            self.assertEqual(
                check_repo_argv.read_text(encoding="utf-8").splitlines(),
                [
                    "--project-root",
                    str(h.host),
                    "--platform-root",
                    str(h.platform),
                    "--allowlist-dir",
                    str(h.config_dir / "allowlists"),
                ],
            )
        finally:
            h.close()

    def test_check_fails_when_engine_conformance_reports_json_false(self) -> None:
        h = HostEntryHarness()
        fake_bin = h.root / "fake-framework"
        fake_bin.write_text(
            "#!/usr/bin/env bash\n"
            "if [ \"${1:-}\" = \"conformance\" ]; then\n"
            "  printf '%s\\n' '{\"ok\":false,\"violations\":[{\"rule\":\"probe\"}]}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        try:
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/bin_bootstrap.sh
                    source host/host_run.sh
                    source host/host_entry.sh
                    ROOT="$PWD"
                    resolve_bin() {{ BIN={shell_quote(fake_bin)}; export BIN; }}
                    ensure_fresh_bin() {{ :; }}
                    host_entry_run_shared_source_ratchets() {{ :; }}
                    host_entry_build_package_roots() {{ HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS=(--package-root {shell_quote(h.local_packages / "site-board")}); }}
                    cmd_host --host-root {shell_quote(h.host)} --platform-root {shell_quote(h.platform)} -- check
                    """
                )
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('"ok":false', result.stdout)
            self.assertIn("conformance reported ok=false", result.stderr)
        finally:
            h.close()

    def test_host_test_runs_full_graph_conformance_but_only_host_owned_unit_tests(self) -> None:
        h = HostEntryHarness()
        fake_bin = h.root / "fake-framework"
        engine_log = h.root / "engine-log"
        fake_bin.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "{ printf 'CMD\\n'; for arg in \"$@\"; do printf '%s\\n' \"$arg\"; done; printf 'END\\n'; } >> "
            + shell_quote(engine_log)
            + "\n"
            "if [ \"${1:-}\" = \"manifest\" ]; then\n"
            "  manifest=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--manifest\" ]; then shift; manifest=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  case \"$manifest\" in\n"
            "    */site-board/fkst.toml) printf 'idle-detector\\n'; exit 0 ;;\n"
            "    *) exit 10 ;;\n"
            "  esac\n"
            "fi\n"
            "if [ \"${1:-}\" = \"--self-test\" ]; then\n"
            "  coverage=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--coverage\" ]; then shift; coverage=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  if [ -n \"$coverage\" ]; then mkdir -p \"$coverage\"; printf '{\"files\":[]}\\n' > \"$coverage/coverage.json\"; fi\n"
            "  printf '0 passed, 0 failed\\n'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = \"conformance\" ]; then\n"
            "  printf '%s\\n' '{\"ok\":true}'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = \"test\" ]; then\n"
            "  report=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--report-json\" ]; then shift; report=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  if [ -n \"$report\" ]; then printf '%s\\n' '{\"schema\":\"fkst.test.report.v1\",\"summary\":{\"passed\":1,\"failed\":0},\"tests\":[{\"owner_namespace\":\"site-board\",\"file\":\"tests/site_test.lua\",\"name\":\"test_site\",\"status\":\"pass\"}]}' > \"$report\"; fi\n"
            "  printf '1 passed, 0 failed\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        try:
            (h.local_packages / "site-board" / "fkst.toml").write_text(
                'kind = "package.composed"\nname = "site-board"\n\n[code]\nroot = "."\n',
                encoding="utf-8",
            )
            (h.host / ".fkst" / "compose" / "package-roots").write_text(
                ".fkst/local-packages/site-board\nfkst-packages:packages/idle-detector\n",
                encoding="utf-8",
            )
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/bin_bootstrap.sh
                    source host/host_run.sh
                    source host/host_entry.sh
                    ROOT="$PWD"
                    resolve_bin() {{ BIN={shell_quote(fake_bin)}; export BIN; }}
                    ensure_fresh_bin() {{ :; }}
                    cmd_host --host-root {shell_quote(h.host)} --platform-root {shell_quote(h.platform)} -- test
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            blocks = self.read_engine_command_blocks(engine_log)

            conformance = [block for block in blocks if block and block[0] == "conformance"]
            tests = [block for block in blocks if block and block[0] == "test"]
            self.assertEqual(
                conformance,
                [
                    [
                        "conformance",
                        "--project-root",
                        str(h.host),
                        "--package-root",
                        str(h.local_packages / "site-board"),
                        "--package-root",
                        str(h.platform / "packages" / "idle-detector"),
                    ]
                ],
            )
            self.assertEqual(len(tests), 1, blocks)
            self.assertEqual(
                tests[0][:5],
                [
                    "test",
                    "--project-root",
                    str(h.host),
                    "--package-root",
                    str(h.local_packages / "site-board"),
                ],
            )
            self.assertNotIn(str(h.platform / "packages" / "idle-detector"), tests[0])
        finally:
            h.close()

    def test_host_test_runs_host_run_graph_tests_with_configured_platform_roots(self) -> None:
        h = HostEntryHarness()
        fake_bin = h.root / "fake-framework"
        engine_log = h.root / "engine-log"
        fake_bin.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "{ printf 'CMD\\n'; for arg in \"$@\"; do printf '%s\\n' \"$arg\"; done; printf 'END\\n'; } >> "
            + shell_quote(engine_log)
            + "\n"
            "if [ \"${1:-}\" = \"manifest\" ]; then\n"
            "  manifest=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--manifest\" ]; then shift; manifest=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  case \"$manifest\" in\n"
            "    */site-board/fkst.toml) printf 'idle-detector\\n'; exit 0 ;;\n"
            "    *) exit 10 ;;\n"
            "  esac\n"
            "fi\n"
            "if [ \"${1:-}\" = \"--self-test\" ]; then\n"
            "  coverage=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--coverage\" ]; then shift; coverage=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  if [ -n \"$coverage\" ]; then mkdir -p \"$coverage\"; printf '{\"files\":[]}\\n' > \"$coverage/coverage.json\"; fi\n"
            "  printf '0 passed, 0 failed\\n'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = \"conformance\" ]; then\n"
            "  printf '%s\\n' '{\"ok\":true}'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = \"test\" ]; then\n"
            "  report=''\n"
            "  roots=()\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--report-json\" ]; then shift; report=\"${1:-}\"; fi\n"
            "    if [ \"${1:-}\" = \"--package-root\" ]; then shift; roots+=(\"${1:-}\"); fi\n"
            "    shift || true\n"
            "  done\n"
            "  run_graph_roots=0\n"
            "  dep_tests=0\n"
            "  for root in \"${roots[@]}\"; do\n"
            "    if ls \"$root\"/tests/run_graph*_test.lua >/dev/null 2>&1; then run_graph_roots=$((run_graph_roots + 1)); fi\n"
            "  done\n"
            "  if [ \"$run_graph_roots\" -gt 0 ] && [ \"${#roots[@]}\" -lt 2 ]; then\n"
            "    printf 'FAIL host run_graph test executed without platform roots\\n'\n"
            "    exit 1\n"
            "  fi\n"
            "  if [ \"$run_graph_roots\" -gt 0 ]; then\n"
            "    for root in \"${roots[@]}\"; do\n"
            "      if [ \"$(basename \"$root\")\" != \"site-board\" ] && [ -d \"$root/tests\" ]; then dep_tests=$((dep_tests + 1)); fi\n"
            "    done\n"
            "    if [ \"$dep_tests\" -ne 0 ]; then\n"
            "      printf 'FAIL graph dependency tests were not stripped\\n'\n"
            "      exit 1\n"
            "    fi\n"
            "  fi\n"
            "  if [ -n \"$report\" ]; then printf '%s\\n' '{\"schema\":\"fkst.test.report.v1\",\"summary\":{\"passed\":1,\"failed\":0},\"tests\":[{\"owner_namespace\":\"site-board\",\"file\":\"tests/site_test.lua\",\"name\":\"test_site\",\"status\":\"pass\"}]}' > \"$report\"; fi\n"
            "  printf '1 passed, 0 failed\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        try:
            (h.local_packages / "site-board" / "fkst.toml").write_text(
                'kind = "package.composed"\nname = "site-board"\n\n[code]\nroot = "."\n',
                encoding="utf-8",
            )
            tests = h.local_packages / "site-board" / "tests"
            tests.mkdir()
            (tests / "site_test.lua").write_text("return { test_site = function() end }\n", encoding="utf-8")
            (tests / "run_graph_platform_test.lua").write_text(
                "return { test_run_graph_platform = function() end }\n",
                encoding="utf-8",
            )
            platform_tests = h.platform / "packages" / "idle-detector" / "tests"
            platform_tests.mkdir()
            (platform_tests / "platform_test.lua").write_text(
                "return { test_platform = function() end }\n",
                encoding="utf-8",
            )
            (h.host / ".fkst" / "compose" / "package-roots").write_text(
                ".fkst/local-packages/site-board\nfkst-packages:packages/idle-detector\n",
                encoding="utf-8",
            )

            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/bin_bootstrap.sh
                    source host/host_run.sh
                    source host/host_entry.sh
                    ROOT="$PWD"
                    resolve_bin() {{ BIN={shell_quote(fake_bin)}; export BIN; }}
                    ensure_fresh_bin() {{ :; }}
                    cmd_host --host-root {shell_quote(h.host)} --platform-root {shell_quote(h.platform)} -- test
                    """
                )
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            blocks = self.read_engine_command_blocks(engine_log)

            tests_blocks = [block for block in blocks if block and block[0] == "test"]
            self.assertEqual(len(tests_blocks), 2, blocks)
            normal_roots = [
                tests_blocks[0][index + 1]
                for index, value in enumerate(tests_blocks[0])
                if value == "--package-root"
            ]
            graph_roots = [
                tests_blocks[1][index + 1]
                for index, value in enumerate(tests_blocks[1])
                if value == "--package-root"
            ]
            self.assertEqual(len(normal_roots), 1)
            self.assertEqual(sorted(Path(root).name for root in graph_roots), ["idle-detector", "site-board"])
            self.assertNotIn(str(h.platform / "packages" / "idle-detector"), tests_blocks[0])
            self.assertNotIn(str(h.platform / "packages" / "idle-detector"), tests_blocks[1])
        finally:
            h.close()

    def test_host_test_runs_platform_unit_tests_when_host_is_platform(self) -> None:
        h = HostEntryHarness()
        fake_bin = h.root / "fake-framework"
        engine_log = h.root / "engine-log"
        fake_bin.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "{ printf 'CMD\\n'; for arg in \"$@\"; do printf '%s\\n' \"$arg\"; done; printf 'END\\n'; } >> "
            + shell_quote(engine_log)
            + "\n"
            "if [ \"${1:-}\" = \"manifest\" ]; then\n"
            "  exit 10\n"
            "fi\n"
            "if [ \"${1:-}\" = \"--self-test\" ]; then\n"
            "  coverage=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--coverage\" ]; then shift; coverage=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  if [ -n \"$coverage\" ]; then mkdir -p \"$coverage\"; printf '{\"files\":[]}\\n' > \"$coverage/coverage.json\"; fi\n"
            "  printf '0 passed, 0 failed\\n'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = \"conformance\" ]; then\n"
            "  printf '%s\\n' '{\"ok\":true}'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = \"test\" ]; then\n"
            "  report=''\n"
            "  while [ \"$#\" -gt 0 ]; do\n"
            "    if [ \"${1:-}\" = \"--report-json\" ]; then shift; report=\"${1:-}\"; fi\n"
            "    shift || true\n"
            "  done\n"
            "  if [ -n \"$report\" ]; then printf '%s\\n' '{\"schema\":\"fkst.test.report.v1\",\"summary\":{\"passed\":1,\"failed\":0},\"tests\":[{\"owner_namespace\":\"own-host\",\"file\":\"tests/package_test.lua\",\"name\":\"test_package\",\"status\":\"pass\"}]}' > \"$report\"; fi\n"
            "  printf '1 passed, 0 failed\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_bin.chmod(0o755)
        try:
            for package in ("github-proxy", "idle-detector"):
                h.make_package(h.host / "packages" / package, package)
            result = h.run_helper(
                textwrap.dedent(
                    f"""\
                    set -euo pipefail
                    source host/bin_bootstrap.sh
                    source host/host_run.sh
                    source host/host_entry.sh
                    ROOT="$PWD"
                    resolve_bin() {{ BIN={shell_quote(fake_bin)}; export BIN; }}
                    ensure_fresh_bin() {{ :; }}
                    cmd_host --host-root {shell_quote(h.host)} --platform-root {shell_quote(h.host)} --local-packages {shell_quote(h.local_packages)} -- test
                    """
                )
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OK: 2 host package(s)", result.stdout)
            blocks = self.read_engine_command_blocks(engine_log)

            tests = [block for block in blocks if block and block[0] == "test"]
            tested_roots = [
                block[block.index("--package-root") + 1]
                for block in tests
                if "--package-root" in block
            ]
            tested_project_roots = [
                block[block.index("--project-root") + 1]
                for block in tests
                if "--project-root" in block
            ]
            self.assertEqual(
                sorted(tested_roots),
                sorted(
                    [
                        str(h.host / "packages" / "github-proxy"),
                        str(h.host / "packages" / "idle-detector"),
                    ]
                ),
            )
            self.assertEqual(sorted(tested_project_roots), sorted(tested_roots))
            self.assertNotIn(str(h.local_packages / "site-board"), tested_roots)
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
