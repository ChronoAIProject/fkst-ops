#!/usr/bin/env python3
"""Behavior tests for pinned fkst-framework bootstrap fallback."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_FIXTURE_ROOT = Path("/Users/auric/fkst-packages")


def write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class BootstrapHarness:
    def __init__(self, pin: str = "dev") -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        (self.root / ".fkst").mkdir()
        (self.root / "scripts").mkdir()
        shutil.copy2(REPO_ROOT / "host" / "bin_cache.py", self.root / "scripts" / "bin_cache.py")
        (self.root / ".fkst" / "substrate-ref").write_text(pin + "\n", encoding="utf-8")
        self.fake_bin = Path(self.tmp.name) / "fake-bin"
        self.fake_bin.mkdir()
        self.cache = Path(self.tmp.name) / "cache"
        self.log = Path(self.tmp.name) / "calls.log"
        self.env = os.environ.copy()
        self.env.pop("FKST_NO_AUTOBUILD", None)
        self.env.update(
            {
                "FKST_BIN_CACHE_ROOT": str(self.cache),
                "FKST_TEST_COMMAND_LOG": str(self.log),
                "PATH": f"{self.fake_bin}{os.pathsep}{self.env.get('PATH', '')}",
            }
        )
        self._install_fake_tools()

    def close(self) -> None:
        self.tmp.cleanup()

    def _install_fake_tools(self) -> None:
        write_executable(
            self.fake_bin / "git",
            textwrap.dedent(
                """\
                #!/usr/bin/env sh
                echo "git $*" >> "$FKST_TEST_COMMAND_LOG"
                if [ "$1" = "clone" ]; then
                  dir="$4"
                  mkdir -p "$dir/.git"
                  exit 0
                fi
                if [ "$1" = "-C" ]; then
                  exit 0
                fi
                exit 1
                """
            ),
        )
        write_executable(
            self.fake_bin / "cargo",
            textwrap.dedent(
                """\
                #!/usr/bin/env sh
                echo "cargo $*" >> "$FKST_TEST_COMMAND_LOG"
                manifest=""
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--manifest-path" ]; then
                    manifest="$2"
                    break
                  fi
                  shift
                done
                checkout="${manifest%/Cargo.toml}"
                mkdir -p "$checkout/target/debug"
                printf '#!/usr/bin/env sh\\n' > "$checkout/target/debug/fkst-framework"
                chmod +x "$checkout/target/debug/fkst-framework"
                exit 0
                """
            ),
        )

    def bootstrap(self, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        if extra_env:
            env.update(extra_env)
        command = (
            f'. "{REPO_ROOT / "host" / "bin_bootstrap.sh"}"; '
            f'bootstrap_bin_on_total_miss "{self.root}"'
        )
        return subprocess.run(
            ["/bin/bash", "-c", command],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""


class BinBootstrapTest(unittest.TestCase):
    def test_all_miss_bootstrap_clones_checks_out_builds_and_returns_binary(self) -> None:
        h = BootstrapHarness("dev")
        try:
            result = h.bootstrap()
            self.assertEqual(result.returncode, 0, result.stderr)
            bin_path = Path(result.stdout.strip())
            self.assertTrue(bin_path.is_file())
            self.assertTrue(os.access(bin_path, os.X_OK))
            calls = h.calls()
            self.assertIn("git clone --no-checkout https://github.com/ChronoAIProject/fkst-substrate.git", calls)
            self.assertIn("git -C", calls)
            self.assertIn(" fetch --tags origin ", calls)
            self.assertIn(" checkout --detach dev", calls)
            self.assertIn("cargo build --manifest-path", calls)
        finally:
            h.close()

    def test_second_run_reuses_checkout_without_reclone(self) -> None:
        h = BootstrapHarness("dev")
        try:
            first = h.bootstrap()
            self.assertEqual(first.returncode, 0, first.stderr)
            self.log_truncate(h.log)
            second = h.bootstrap()
            self.assertEqual(second.returncode, 0, second.stderr)
            calls = h.calls()
            self.assertNotIn("git clone", calls)
            self.assertIn("git -C", calls)
            self.assertIn("cargo build --manifest-path", calls)
            self.assertEqual(first.stdout.strip(), second.stdout.strip())
        finally:
            h.close()

    def test_no_autobuild_errors_without_git_or_cargo_calls(self) -> None:
        h = BootstrapHarness("dev")
        try:
            result = h.bootstrap({"FKST_NO_AUTOBUILD": "1"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FKST_NO_AUTOBUILD is set", result.stderr)
            self.assertEqual(h.calls(), "")
        finally:
            h.close()

    def test_failed_build_releases_lock_for_retry(self) -> None:
        h = BootstrapHarness("dev")
        try:
            write_executable(
                h.fake_bin / "cargo",
                "#!/usr/bin/env sh\n"
                "echo \"cargo $*\" >> \"$FKST_TEST_COMMAND_LOG\"\n"
                "exit 42\n",
            )
            failed = h.bootstrap()
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(list(h.cache.rglob("*.lock")))

            h._install_fake_tools()
            retried = h.bootstrap()
            self.assertEqual(retried.returncode, 0, retried.stderr)
        finally:
            h.close()

    def test_missing_git_reports_narrow_tool_error(self) -> None:
        h = BootstrapHarness("dev")
        try:
            (h.fake_bin / "git").unlink()
            result = h.bootstrap({"PATH": str(h.fake_bin)})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required tool missing", result.stderr)
            self.assertIn("git", result.stderr)
        finally:
            h.close()

    def test_owner_repo_ref_and_short_ref_use_distinct_cache_dirs(self) -> None:
        short = BootstrapHarness("dev")
        full = BootstrapHarness("Other/fkst-substrate@dev")
        try:
            short_result = short.bootstrap()
            full_result = full.bootstrap()
            self.assertEqual(short_result.returncode, 0, short_result.stderr)
            self.assertEqual(full_result.returncode, 0, full_result.stderr)
            self.assertNotEqual(short_result.stdout.strip(), full_result.stdout.strip())
            self.assertIn("/ChronoAIProject/fkst-substrate/dev/", short_result.stdout.strip())
            self.assertIn("/Other/fkst-substrate/dev/", full_result.stdout.strip())
        finally:
            short.close()
            full.close()

    @staticmethod
    def log_truncate(path: Path) -> None:
        path.write_text("", encoding="utf-8")


class RunScriptResolutionContractTest(unittest.TestCase):
    def run_script(self, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        command = (
            f'. "{REPO_ROOT / "host" / "bin_bootstrap.sh"}"; '
            f'. "{HOST_FIXTURE_ROOT / "scripts" / "run_bin.sh"}"; '
            'ROOT="$PWD"; local_iteration_result_fail() { :; }; resolve_bin; printf "%s\\n" "$BIN"'
        )
        return subprocess.run(
            ["/bin/bash", "-c", command],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_invalid_explicit_bin_errors_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = os.environ.copy()
            env.update({"BIN": str(root / "missing"), "FKST_BIN_CACHE_ROOT": str(root / "cache")})
            result = self.run_script(root, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("explicit BIN is not executable", result.stderr)
            self.assertFalse((root / "cache").exists())

    def test_invalid_env_bin_errors_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".fkst").mkdir()
            (root / ".fkst" / "substrate-ref").write_text("dev\n", encoding="utf-8")
            env_path = root / ".fkst" / "env"
            env = os.environ.copy()
            env.pop("BIN", None)
            env.update({"FKST_BIN_CACHE_ROOT": str(Path(tmp) / "cache")})
            env_path.write_text(f"BIN={Path(tmp) / 'missing'}\n", encoding="utf-8")
            try:
                result = self.run_script(root, env)
            finally:
                env_path.unlink()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".fkst/env BIN is not executable", result.stderr)
            self.assertFalse((Path(tmp) / "cache").exists())


if __name__ == "__main__":
    unittest.main()
