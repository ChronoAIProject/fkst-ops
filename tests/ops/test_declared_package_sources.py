"""A declared package source reaches the launch contract, and drives reload when it moves.

Both cases run the operator's own functions, extracted from the script rather than restated
here: a test that re-implements the logic it checks passes whatever the operator does.

The second is what makes "update the source and it reloads" true. The engine already logs a
commit per loaded package, and packages from one source all carry that source's commit, so the
question "has this source moved" is answered by comparing one of its packages against the
source's current head — no new record, and nothing the running process reports about itself.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
SOURCE_CONTROL = ROOT / "ops" / "deployment_source_control.sh"


def extract(*functions: str) -> str:
    """Load named operator functions, failing loudly when one is absent.

    Without the guard, a test whose expectation is "no output" would pass against an operator
    that never defined the function at all.
    """
    owners = {}
    for name in functions:
        owners[name] = next(
            path
            for path in (OPERATOR, SOURCE_CONTROL)
            if f"\n{name}()" in f"\n{path.read_text(encoding='utf-8')}"
        )
    return "".join(
        f'eval "$(sed -n \'/^{name}()/,/^}}/p\' "{owners[name]}")"\n'
        f'declare -F {name} >/dev/null || {{ echo "operator has no {name}" >&2; exit 3; }}\n'
        for name in functions
    )


def run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True)


def preamble(declared: object) -> str:
    return (
        f"PYTHON={sys.executable!r}\n"
        f"DECLARED_PACKAGE_SOURCES={json.dumps(declared) if not isinstance(declared, str) else declared!r}\n"
    )


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def make_source(root: Path, name: str) -> Path:
    """An origin plus a checkout of it, so `origin/<branch>` means what it means in production."""
    origin = root / f"{name}.git"
    work = root / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "integration", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "integration", str(work)], check=True)
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        git(work, "config", key, value)
    (work / "packages").mkdir()
    (work / "packages" / "marker").write_text("1\n", encoding="ascii")
    git(work, "add", ".")
    git(work, "commit", "-qm", "first")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "integration")
    git(work, "fetch", "-q", "origin")
    return work


class DeclaredPackageSourceTest(unittest.TestCase):
    def test_declared_sources_become_launch_arguments(self) -> None:
        declared = [
            {"root": "/srv/extra", "packages": ["site-board", "site-radar"]},
            {"root": "/srv/second", "packages": ["site-clock"]},
        ]
        result = run(extract("package_source_launch_args") + preamble(declared)
                     + "package_source_launch_args\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["--package-source", "/srv/extra", "site-board site-radar",
             "--package-source", "/srv/second", "site-clock"],
        )

    def test_no_declared_sources_produce_no_launch_arguments(self) -> None:
        for declared in ("[]", ""):
            with self.subTest(declared=declared):
                result = run(extract("package_source_launch_args") + preamble(declared)
                             + "package_source_launch_args\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_the_staleness_probe_names_one_package_per_source(self) -> None:
        declared = [
            {"root": "/srv/extra", "packages": ["site-board", "site-radar"]},
            {"root": "/srv/second", "packages": ["site-clock"]},
        ]
        result = run(extract("package_source_probes") + preamble(declared)
                     + "package_source_probes\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["/srv/extra\tsite-board\t", "/srv/second\tsite-clock\t"],
        )

    def test_package_source_config_status_names_the_source_and_checkout_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            head = git(work, "rev-parse", "HEAD")
            declared = [{
                "root": str(work),
                "packages": ["site-board", "site-radar"],
            }]
            result = run(
                extract("package_source_config_status")
                + preamble(declared)
                + "INTEGRATION_BRANCH=integration\n"
                + "package_source_config_status\n"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"root={work}", result.stdout)
        self.assertIn("packages=site-board,site-radar", result.stdout)
        self.assertIn(f"checkout={head}", result.stdout)

    def _verdict(
        self, work: Path, logged: str, temp: Path, resolved: dict | None = None
    ) -> subprocess.CompletedProcess:
        log = temp / "supervise.log"
        log.write_text(f"EVENT=code_provenance PKG_VERS=site-board@{logged}\n", encoding="ascii")
        declared = [{"root": str(work), "packages": ["site-board"]}]
        if resolved is not None:
            declared[0]["resolved"] = resolved
        return run(
            extract(
                "package_source_probes",
                "provenance_package_versions",
                "provenance_package_version",
                "package_source_moved",
            )
            + preamble(declared)
            + "INTEGRATION_BRANCH=integration\n"
            + f'package_versions=$(provenance_package_versions "{log}")\n'
            + 'package_source_moved "$package_versions"\n'
        )

    def test_an_unmoved_package_source_is_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            head = git(work, "rev-parse", "origin/integration")
            result = self._verdict(work, head[:12], temp)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", "an unmoved source must not force a reload")

    def test_each_producer_version_shape_has_an_explicit_non_looping_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            head = git(work, "rev-parse", "origin/integration")
            cases = (
                (head[:12], ""),
                (head[:12] + "-dirty", "pkg-provenance-dirty"),
                ("unknown", "pkg-provenance-unknown"),
                ("not-a-revision", "pkg-provenance-invalid"),
            )
            actuator_verdicts = {"pkg-stale", "engine-stale", "environment-stale"}
            for logged, expected in cases:
                with self.subTest(logged=logged):
                    result = self._verdict(work, logged, temp)
                    verdict = result.stdout.strip()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(verdict, expected)
                    if expected:
                        self.assertNotIn(
                            verdict,
                            actuator_verdicts,
                            "an unchangeable provenance state must not trigger restart",
                        )
                        self.assertIn(str(work), result.stderr)
                        self.assertIn(logged, result.stderr)

    def test_a_moved_package_source_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            loaded = git(work, "rev-parse", "origin/integration")
            (work / "packages" / "marker").write_text("2\n", encoding="ascii")
            git(work, "add", ".")
            git(work, "commit", "-qm", "second")
            git(work, "push", "-q", "origin", "integration")
            result = self._verdict(work, loaded[:12], temp)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "pkg-stale")

    def test_an_exact_pinned_source_ignores_a_different_branch_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            pinned = git(work, "rev-parse", "HEAD")
            (work / "packages" / "marker").write_text("2\n", encoding="ascii")
            git(work, "add", ".")
            git(work, "commit", "-qm", "branch advances past pin")
            git(work, "push", "-q", "origin", "integration")
            result = self._verdict(
                work,
                pinned[:12],
                temp,
                {"rev": pinned},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_semicolon_provenance_suffix_pair_binds_package_by_exact_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "EVENT=code_provenance "
                "PKG_VERS=autochrono@111111111111;consensus@111111111111;"
                "github-autochrono@111111111111;host@222222222222;"
                "site-autochrono@999999999999\n",
                encoding="ascii",
            )
            declared = [{
                "root": "/source",
                "packages": ["autochrono"],
                "resolved": {
                    "rev": "111111111111" + "0" * 28,
                },
            }]
            result = run(
                extract(
                    "package_source_probes",
                    "provenance_package_versions",
                    "provenance_package_version",
                    "package_source_moved",
                )
                + preamble(declared)
                + f'package_versions=$(provenance_package_versions "{log}")\n'
                + 'package_source_moved "$package_versions"\n'
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", "a suffix package must not alias the exact name")

    def test_semicolon_provenance_suffix_pair_binds_platform_by_exact_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "EVENT=code_provenance "
                "PKG_VERS=autochrono@111111111111;consensus@111111111111;"
                "github-autochrono@111111111111;host@222222222222;"
                "site-autochrono@999999999999 "
                f"ENGINE_VER=aaaaaaaa LAUNCH_ENV_SHA256={'1' * 64}\n",
                encoding="ascii",
            )
            result = run(
                extract(
                    "source_pin_values",
                    "package_source_probes",
                    "provenance_package_versions",
                    "provenance_package_version",
                    "package_source_moved",
                    "_proc_stale",
                )
                + preamble([])
                + f'''cfg() {{
  PKGSRC=/platform; INTEGRATION_BRANCH=integration
  PLATFORM_SOURCE_PIN='{{"rev":"111111111111{'0' * 28}"}}'
}}
pidof_df() {{ echo 123; }}
latest_log() {{ echo {str(log)!r}; }}
PLATFORM_PKGS=autochrono
resolve_engine_pair() {{ ENGINE_REVISION=aaaaaaaa; }}
resolve_deployment_child_environment() {{ :; }}
deployment_child_environment_sha256() {{ printf '%s\n' {'1' * 64!r}; }}
git() {{ [ "$3" != diff ] || echo packages/autochrono/src.lua; }}
_proc_stale deployment
'''
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "current")

    def test_a_source_absent_from_the_log_is_invalid_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            log = temp / "supervise.log"
            log.write_text("EVENT=code_provenance PKG_VERS=consensus@abcdef123456\n", encoding="ascii")
            declared = [{"root": str(work), "packages": ["site-board"]}]
            result = run(
                extract(
                    "package_source_probes",
                    "provenance_package_versions",
                    "provenance_package_version",
                    "package_source_moved",
                )
                + preamble(declared)
                + "INTEGRATION_BRANCH=integration\n"
                + f'package_versions=$(provenance_package_versions "{log}")\n'
                + 'package_source_moved "$package_versions"\n'
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "pkg-provenance-invalid")
        self.assertIn("has no provenance binding", result.stderr)

    def test_git_probe_failures_are_invalid_provenance(self) -> None:
        for failure in ("fetch", "rev-parse"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                log = Path(directory) / "supervise.log"
                log.write_text(
                    "EVENT=code_provenance PKG_VERS=site-board@11111111\n",
                    encoding="ascii",
                )
                declared = [{"root": "/source", "packages": ["site-board"]}]
                result = run(
                    extract(
                        "package_source_probes",
                        "provenance_package_versions",
                        "provenance_package_version",
                        "package_source_moved",
                    )
                    + preamble(declared)
                    + f'''INTEGRATION_BRANCH=integration
FAILURE={failure!r}
git() {{
  case "$*" in
    *" fetch "*) [ "$FAILURE" != fetch ] ;;
    *" rev-parse "*) [ "$FAILURE" != rev-parse ] && echo 11111111 ;;
    *) return 1 ;;
  esac
}}
package_versions=$(provenance_package_versions "{log}")
package_source_moved "$package_versions"
'''
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "pkg-provenance-invalid")
                self.assertIn(str(declared[0]["root"]), result.stderr)

    def test_probe_enumeration_failure_is_invalid_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "EVENT=code_provenance PKG_VERS=site-board@11111111\n",
                encoding="ascii",
            )
            result = run(
                extract(
                    "package_source_probes",
                    "provenance_package_versions",
                    "provenance_package_version",
                    "package_source_moved",
                )
                + preamble("not-json")
                + f'package_versions=$(provenance_package_versions "{log}")\n'
                + 'package_source_moved "$package_versions"\n'
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "pkg-provenance-invalid")

    def test_launch_argument_enumeration_failure_blocks_launch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            marker = temp / "launch-child-called"
            fake_python = temp / "python"
            fake_python.write_text(
                f"#!/bin/sh\ntouch {str(marker)!r}\necho 123\n",
                encoding="ascii",
            )
            fake_python.chmod(0o755)
            (temp / "logs").mkdir()
            result = run(
                extract("launch_one")
                + f'''PYTHON={str(fake_python)!r}; _self_dir=/ops; _repo_root=/repo
LOGDIR={str(temp / 'logs')!r}; RUNTIME_ROOT={str(temp / 'runtime')!r}
PLATFORM_REVISION=platform-rev; ENGINE_REVISION=engine-rev
BIN={str(temp / 'bin' / 'engine')!r}; ENGINE_BINARY_BASE="$BIN"
DUR={str(temp / 'durable')!r}; HOST=/host; PKGSRC=/platform
PLATFORM_PKGS=autochrono
GITHUB_WRITER_LOGIN=bot
CLAIM_MODE=assignee; CLAIM_LABEL_EXCLUSIVE=0; DEPLOYMENT_CHILD_ENVIRONMENT=()
clean_stale_engine_artifacts() {{ :; }}
cfg() {{ :; }}
ensure_engine_binary_current() {{ :; }}
authorize_github_writer() {{ :; }}
resolve_deployment_child_environment() {{ :; }}
deployment_child_environment_sha256() {{ echo {'1' * 64!r}; }}
clean_stale_launch_platforms() {{ :; }}
materialise_launch_platform() {{ :; }}
engine_build_receipt_current() {{ :; }}
package_source_launch_args() {{ return 9; }}
wait_supervise_ready() {{ :; }}
clean_stale_runtime_worktrees() {{ :; }}
engine_panic_count() {{ echo 0; }}
launch_one deployment 0
'''
            )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "launch continued without declared package sources")

    def test_package_sources_participate_in_checkout_repair_and_lock_sweep(self) -> None:
        declared = [{
            "root": "/extra",
            "git": "https://example.invalid/extra.git",
            "packages": ["site-board"],
        }]
        common = preamble(declared) + '''
HOST=/platform; PKGSRC=/platform; PLATFORM_GIT_URL=platform-url; TARGET_GIT_URL=target-url
PLATFORM_SOURCE_PIN=; TARGET_SOURCE_PIN=; UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
RUNTIME_ROOT=/runtime; DUR=/durable
cfg() { :; }
ensure_integration_caught_up() { :; }
sync_to_run_branch() { :; }
sync_deployment_source() { :; }
sync_declared_package_sources() { :; }
bin_ensure_fresh() { :; }
_proc_stale() { echo stopped; }
launch_with_lock_retry() { :; }
'''
        restart = run(
            extract(
                "declared_package_source_checkouts",
                "ensure_declared_package_source_checkouts",
                "restart_one",
            )
            + common
            + '''ensure_run_checkout() { printf 'ensure:%s:%s\n' "$1" "$2"; }
restart_one deployment
'''
        )
        sweep = run(
            extract(
                "declared_package_source_checkouts",
                "ensure_declared_package_source_checkouts",
                "cmd_sync",
            )
            + common
            + '''ensure_run_checkout() { printf 'ensure:%s:%s\n' "$1" "$2"; }
expand() { echo deployment; }
git_lock_sweep() { printf 'sweep'; shift; printf ':%s' "$@"; printf '\n'; }
cmd_sync all
'''
        )

        self.assertEqual(restart.returncode, 0, restart.stdout + restart.stderr)
        self.assertIn(
            "ensure:/extra:https://example.invalid/extra.git", restart.stdout
        )
        self.assertEqual(sweep.returncode, 0, sweep.stdout + sweep.stderr)
        self.assertIn("ensure:/extra:https://example.invalid/extra.git", sweep.stdout)
        self.assertIn(":/extra", sweep.stdout)

    def test_cmd_sync_reclones_a_missing_declared_package_source_before_sweeping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            package_source = make_source(temp, "extra")
            origin = temp / "extra.git"
            expected = git(package_source, "rev-parse", "HEAD")
            shutil.rmtree(package_source)
            synced = temp / "synced"
            declared = [{
                "root": str(package_source),
                "git": str(origin),
                "packages": ["site-board"],
            }]
            result = run(
                extract(
                    "ensure_run_checkout",
                    "declared_package_source_checkouts",
                    "ensure_declared_package_source_checkouts",
                    "cmd_sync",
                )
                + preamble(declared)
                + f'''PACKAGE_SOURCE={str(package_source)!r}
SYNCED={str(synced)!r}
HOST=/platform; PKGSRC=/platform; RUNTIME_ROOT=/runtime
PLATFORM_SOURCE_PIN=; TARGET_SOURCE_PIN=; INTEGRATION_BRANCH=integration
expand() {{ echo deployment; }}
cfg() {{ :; }}
git_lock_sweep() {{
  git -C "$PACKAGE_SOURCE" rev-parse --git-dir >/dev/null 2>&1 || return 9
}}
ensure_integration_caught_up() {{ :; }}
sync_to_run_branch() {{ :; }}
sync_deployment_source() {{ :; }}
sync_declared_package_sources() {{
  git -C "$PACKAGE_SOURCE" rev-parse --git-dir >/dev/null 2>&1 && touch "$SYNCED"
}}
bin_ensure_fresh() {{ :; }}
_proc_stale() {{ echo stopped; }}
cmd_sync all
'''
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(synced.exists(), "sync did not reach the repaired checkout")
            self.assertEqual(git(package_source, "rev-parse", "HEAD"), expected)
            self.assertEqual(git(package_source, "remote", "get-url", "origin"), str(origin))

    def test_proc_stale_honors_an_exact_package_source_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "extra")
            pinned = git(work, "rev-parse", "HEAD")
            (work / "packages" / "marker").write_text("2\n", encoding="ascii")
            git(work, "add", ".")
            git(work, "commit", "-qm", "branch advances past pin")
            git(work, "push", "-q", "origin", "integration")
            branch_head = git(work, "rev-parse", "HEAD")
            log = temp / "supervise.log"
            log.write_text(
                "EVENT=code_provenance "
                f"PKG_VERS=platform-package@{branch_head[:12]};site-board@{pinned[:12]} "
                f"ENGINE_VER=aaaaaaaa LAUNCH_ENV_SHA256={'1' * 64}\n",
                encoding="ascii",
            )
            declared = [{
                "root": str(work),
                "packages": ["site-board"],
                "resolved": {"rev": pinned},
            }]
            result = run(
                extract(
                    "package_source_probes",
                    "provenance_package_versions",
                    "provenance_package_version",
                    "package_source_moved",
                    "_proc_stale",
                )
                + preamble(declared)
                + f'''cfg() {{ PKGSRC={str(work)!r}; INTEGRATION_BRANCH=integration; PLATFORM_SOURCE_PIN=; }}
pidof_df() {{ echo 123; }}
latest_log() {{ echo {str(log)!r}; }}
PLATFORM_PKGS=platform-package
resolve_engine_pair() {{ ENGINE_REVISION=aaaaaaaa; }}
resolve_deployment_child_environment() {{ :; }}
deployment_child_environment_sha256() {{ printf '%s\n' {'1' * 64!r}; }}
_proc_stale deployment
'''
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "current")

    def test_package_source_advance_converges_after_exactly_one_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            platform = make_source(temp, "platform")
            platform_revision = git(platform, "rev-parse", "HEAD")

            publisher = make_source(temp, "publisher")
            origin = temp / "publisher.git"
            package_source = temp / "package-source"
            subprocess.run(
                ["git", "clone", "-q", "-b", "integration", str(origin), str(package_source)],
                check=True,
            )
            loaded_revision = git(package_source, "rev-parse", "HEAD")

            (publisher / "packages" / "marker").write_text("2\n", encoding="ascii")
            git(publisher, "add", ".")
            git(publisher, "commit", "-qm", "advance before cadence")
            git(publisher, "push", "-q", "origin", "integration")
            sync_revision = git(publisher, "rev-parse", "HEAD")

            log = temp / "supervise.log"
            environment_sha256 = "1" * 64
            log.write_text(
                "EVENT=code_provenance "
                f"PKG_VERS=platform-package@{platform_revision[:12]};"
                f"site-board@{loaded_revision[:12]} ENGINE_VER=aaaaaaaa "
                f"LAUNCH_ENV_SHA256={environment_sha256}\n",
                encoding="ascii",
            )
            durable = temp / "durable"
            (durable / ".fkst-supervise.pid").parent.mkdir(parents=True)
            (durable / ".fkst-supervise.pid").write_text("123\n", encoding="ascii")
            restart_count = temp / "restart-count"
            first_probe_head = temp / "first-probe-head"
            advance_marker = temp / "advanced-during-probe"
            declared = [{
                "root": str(package_source),
                "git": str(origin),
                "packages": ["site-board"],
            }]
            command = (
                extract(
                    "ensure_run_checkout",
                    "declared_package_source_checkouts",
                    "ensure_declared_package_source_checkouts",
                    "sync_to_run_branch",
                    "sync_deployment_source",
                    "restart_one",
                    "package_source_probes",
                    "provenance_package_versions",
                    "provenance_package_version",
                    "package_source_moved",
                    "_proc_stale",
                    "cmd_sync",
                )
                # This helper is intentionally optional in the red fixture: pass 1 has no such
                # actuator yet, but the unchanged cmd_sync/restart_one functions must still run.
                + extract("sync_declared_package_sources")
                + f'''
eval "$(declare -f _proc_stale | sed '1s/_proc_stale/_actual_proc_stale/')"
PYTHON={sys.executable!r}
DECLARED_PACKAGE_SOURCES={json.dumps(declared)!r}
HOST={str(platform)!r}; PKGSRC={str(platform)!r}
PLATFORM_GIT_URL={str(temp / "platform.git")!r}; TARGET_GIT_URL=unused
PLATFORM_SOURCE_PIN=; TARGET_SOURCE_PIN=
UPSTREAM_BRANCH=integration; INTEGRATION_BRANCH=integration
RUNTIME_ROOT={str(temp / "runtime")!r}; DUR={str(durable)!r}
TEST_LOG={str(log)!r}; PACKAGE_SOURCE={str(package_source)!r}
PUBLISHER={str(publisher)!r}; EXPECTED_SYNC_REVISION={sync_revision!r}
FIRST_PROBE_HEAD={str(first_probe_head)!r}; ADVANCE_MARKER={str(advance_marker)!r}
RESTART_COUNT={str(restart_count)!r}; ENVIRONMENT_SHA256={environment_sha256!r}
expand() {{ echo deployment; }}
cfg() {{ :; }}
git_lock_sweep() {{ :; }}
PLATFORM_PKGS=platform-package
ensure_integration_caught_up() {{ :; }}
bin_ensure_fresh() {{ echo current; }}
pidof_df() {{ echo 123; }}
latest_log() {{ echo "$TEST_LOG"; }}
resolve_engine_pair() {{ ENGINE_REVISION=aaaaaaaa; }}
resolve_deployment_child_environment() {{ :; }}
deployment_child_environment_sha256() {{ echo "$ENVIRONMENT_SHA256"; }}
launch_with_lock_retry() {{
  git -C "$PACKAGE_SOURCE" rev-parse HEAD >> "$RESTART_COUNT"
  local platform_head package_head
  platform_head=$(git -C "$PKGSRC" rev-parse --short=12 HEAD)
  package_head=$(git -C "$PACKAGE_SOURCE" rev-parse --short=12 HEAD)
  printf 'EVENT=code_provenance PKG_VERS=platform-package@%s;site-board@%s ENGINE_VER=aaaaaaaa LAUNCH_ENV_SHA256=%s\n' \
    "$platform_head" "$package_head" "$ENVIRONMENT_SHA256" > "$TEST_LOG"
}}
_proc_stale() {{
  if [ ! -e "$ADVANCE_MARKER" ]; then
    git -C "$PACKAGE_SOURCE" rev-parse HEAD > "$FIRST_PROBE_HEAD"
    printf '3\n' > "$PUBLISHER/packages/marker"
    git -C "$PUBLISHER" add .
    git -C "$PUBLISHER" commit -qm 'advance during first probe'
    git -C "$PUBLISHER" push -q origin integration
    touch "$ADVANCE_MARKER"
  fi
  _actual_proc_stale "$@"
}}
cmd_sync all
cmd_sync all
'''
            )
            result = run(command)

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            restarts = restart_count.read_text(encoding="ascii").splitlines() \
                if restart_count.exists() else []
            self.assertEqual(
                len(restarts), 1,
                "a moved package source must restart once, then converge:\n" + result.stdout,
            )
            self.assertEqual(
                first_probe_head.read_text(encoding="ascii").strip(),
                sync_revision,
                "cmd_sync must advance the package source before probing",
            )
            final_revision = git(publisher, "rev-parse", "HEAD")
            self.assertEqual(restarts, [final_revision])
            self.assertEqual(git(package_source, "rev-parse", "HEAD"), final_revision)
            self.assertEqual(result.stdout.count("pkg-stale -> auto-restart"), 1)
            self.assertIn("current (no restart needed)", result.stdout)

    def test_declared_package_source_sync_honors_an_exact_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            work = make_source(temp, "pinned")
            pinned_revision = git(work, "rev-parse", "HEAD")
            (work / "packages" / "marker").write_text("2\n", encoding="ascii")
            git(work, "add", ".")
            git(work, "commit", "-qm", "branch advances past exact pin")
            git(work, "push", "-q", "origin", "integration")
            branch_head = git(work, "rev-parse", "HEAD")
            declared = [{
                "root": str(work),
                "packages": ["site-board"],
                "resolved": {"rev": pinned_revision},
            }]
            result = run(
                extract(
                    "source_pin_values",
                    "sync_to_pinned_revision",
                    "sync_deployment_source",
                )
                + extract("sync_declared_package_sources")
                + f'''
PYTHON={sys.executable!r}; _repo_root={str(ROOT)!r}; _self_dir={str(ROOT / "ops")!r}
INTEGRATION_BRANCH=integration
DECLARED_PACKAGE_SOURCES={json.dumps(declared)!r}
sync_declared_package_sources
'''
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(git(work, "rev-parse", "HEAD"), pinned_revision)
            self.assertEqual(git(work, "rev-parse", "origin/integration"), branch_head)

    def test_package_source_sync_failure_stops_both_actuator_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            durable = temp / "durable"
            (durable / ".fkst-supervise.pid").parent.mkdir(parents=True)
            (durable / ".fkst-supervise.pid").write_text("123\n", encoding="ascii")
            launched = temp / "launched"
            common = extract("sync_declared_package_sources") + f'''
PYTHON={sys.executable!r}; DECLARED_PACKAGE_SOURCES='[{{"root":"/bad","git":"bad","packages":["site-board"]}}]'
HOST=/platform; PKGSRC=/platform; PLATFORM_GIT_URL=platform; TARGET_GIT_URL=target
PLATFORM_SOURCE_PIN=; TARGET_SOURCE_PIN=; UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
RUNTIME_ROOT=/runtime; DUR={str(durable)!r}
cfg() {{ :; }}
ensure_integration_caught_up() {{ :; }}
ensure_run_checkout() {{ :; }}
sync_to_run_branch() {{ :; }}
sync_deployment_source() {{ [ "$1" != /bad ]; }}
declared_package_source_checkouts() {{ printf '/bad\tbad\n'; }}
ensure_declared_package_source_checkouts() {{ :; }}
launch_with_lock_retry() {{ touch {str(launched)!r}; }}
'''
            commands = {
                "cmd_sync": (
                    extract("cmd_sync")
                    + common
                    + '''expand() { echo deployment; }
git_lock_sweep() { :; }
bin_ensure_fresh() { echo unexpected-bin; }
_proc_stale() { echo stopped; }
cmd_sync all
'''
                ),
                "restart_one": extract("restart_one") + common + "restart_one deployment\n",
            }
            for callsite, command in commands.items():
                with self.subTest(callsite=callsite):
                    launched.unlink(missing_ok=True)
                    result = run(command)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse(launched.exists(), "launch continued after source sync failure")


if __name__ == "__main__":
    unittest.main()
