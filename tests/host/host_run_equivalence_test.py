#!/usr/bin/env python3
"""Golden-master test for dogfood launch delegation."""

from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import tempfile
import textwrap
import time
import tomllib
import unittest
from pathlib import Path

from host_run_process_group_test import ProcessGroupCleanupTest
from host_run_test_support import run_bounded


REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_FIXTURE_ROOT_ENV = "FKST_HOST_FIXTURE_ROOT"
HOST_FIXTURE_ROOT = Path(value) if (value := os.environ.get(HOST_FIXTURE_ROOT_ENV)) else None
REQUIRES_HOST_FIXTURE = unittest.skipUnless(
    HOST_FIXTURE_ROOT is not None and HOST_FIXTURE_ROOT.is_dir(),
    f"set {HOST_FIXTURE_ROOT_ENV} to the host fixture checkout",
)
GOLDEN_PATH = REPO_ROOT / "tests" / "host" / "host_run_equivalence_golden.json"
TARGETS = ("packages", "substrate", "website")
WEBSITE_PLATFORM_PACKAGES = " ".join(
    (
        "github-devloop",
        "github-devloop-pr",
        "github-devloop-integration",
        "github-devloop-intake",
        "github-devloop-workflow",
        "github-devloop-decompose",
        "github-devloop-ops",
        "github-proxy",
        "github-external-pr-intake",
        "github-ratchet-migration-slicer",
        "idle-detector",
    )
)
STALE_WEBSITE_PACKAGES = "github-devloop github-devloop-pr github-devloop-integration"
FIXED_TS = "1760000000"


def host_fixture_path(*parts: str) -> Path:
    assert HOST_FIXTURE_ROOT is not None
    return HOST_FIXTURE_ROOT.joinpath(*parts)


def self_workspace_platform_packages() -> str:
    workspace = tomllib.loads(host_fixture_path("fkst.workspace.toml").read_text(encoding="utf-8"))
    packages: list[str] = []
    for package in workspace.get("package", []):
        if isinstance(package, dict) and package.get("source", "workspace") == "workspace":
            name = package.get("name")
            if isinstance(name, str) and name:
                packages.append(name)
    if not packages:
        raise AssertionError("fkst.workspace.toml must declare self-host dogfood platform packages")
    return " ".join(packages)


PLATFORM_PACKAGES = self_workspace_platform_packages() if HOST_FIXTURE_ROOT is not None else ""
ALL_PLATFORM_PACKAGES = sorted(set(PLATFORM_PACKAGES.split()) | set(WEBSITE_PLATFORM_PACKAGES.split()))


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def wait_for_process_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def make_fake_bin(path: Path) -> None:
    write_executable(
        path,
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            import time
            from pathlib import Path

            out = Path(os.environ["CAPTURE_FILE"])
            keys = [
                "BIN",
                "FKST_GITHUB_REPO",
                "FKST_GITHUB_WRITE",
                "FKST_GITHUB_BOT_LOGIN",
                "FKST_DEVLOOP_UPSTREAM_BRANCH",
                "FKST_DEVLOOP_INTEGRATION_BRANCH",
                "FKST_DEVLOOP_ROLLUP_MERGE",
                "FKST_DEVLOOP_MANAGED_BOT_LOGINS",
                "FKST_DEVLOOP_LOCAL_TEST_COMMAND",
                "FKST_GITHUB_PROXY_POLL_LABEL_PREFIX",
                "FKST_RUNTIME_ROOT",
                "FKST_DURABLE_ROOT",
                "FKST_RATE_POOL_ROOT",
                "FKST_DEVLOOP_BOARD_CMD",
            ]
            payload = {
                "cwd": os.getcwd(),
                "argv": sys.argv,
                "env": {key: os.environ[key] for key in keys if key in os.environ},
            }
            out.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\\n", encoding="utf-8")
            print("TIMESTAMP=2026-01-01T00:00:00Z LEVEL=info EVENT=code_provenance ENGINE_VER=test-engine PKG_VERS=github-devloop@test-package", flush=True)
            print("TIMESTAMP=2026-01-01T00:00:00Z LEVEL=INFO handles=1 MSG=event runtime running", flush=True)
            time.sleep(15)
            """
        ),
    )


def make_fake_date(bin_dir: Path) -> None:
    write_executable(
        bin_dir / "date",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [ "${{1:-}}" = "+%s" ]; then
              printf '%s\\n' "{FIXED_TS}"
              exit 0
            fi
            exec /bin/date "$@"
            """
        ),
    )


def make_fake_tools(bin_dir: Path) -> None:
    write_executable(
        bin_dir / "cargo",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    make_fake_date(bin_dir)


def run_git(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    result = run_bounded(["git", *args], cwd=cwd, env=env)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")


def git_stdout(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = run_bounded(["git", *args], cwd=cwd, env=env)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout.strip()


class DogfoodLayout:
    def __init__(
        self,
        root: Path,
        dogfood_script: str,
        *,
        stale_website_manifest: bool = False,
    ) -> None:
        self.root = root
        self.dogfood_root = root / "dogfood"
        self.skill_dir = root / "skill"
        self.bin_dir = root / "bin"
        self.capture = root / "capture.json"
        self.fake_bin = root / "fake-fkst-framework"
        self.substrate_src = root / "substrate-src"
        self.script = self.skill_dir / "dogfood.sh"

        self.skill_dir.mkdir(parents=True)
        self.bin_dir.mkdir()
        self.substrate_src.mkdir()
        (self.substrate_src / "crates").mkdir()
        make_fake_tools(self.bin_dir)
        make_fake_bin(self.fake_bin)
        write_executable(self.script, dogfood_script)
        shutil.copy2(
            DOGFOOD_FIXTURE_ROOT / "workspace_manifest.py",
            self.skill_dir / "workspace_manifest.py",
        )
        self.stale_website_manifest = stale_website_manifest
        self.platform_revs: dict[Path, str] = {}
        self._populate_repos()

    def _populate_repos(self) -> None:
        for host in (
            self.dogfood_root / "pkgs-dogfood",
            self.dogfood_root / "substrate-dogfood" / "pkgs",
            self.dogfood_root / "substrate-dogfood" / "sub",
            self.dogfood_root / "website-dogfood" / "pkgs",
            self.dogfood_root / "website-dogfood" / "site",
        ):
            (host / ".git").mkdir(parents=True)

        platform_roots = (
            self.dogfood_root / "pkgs-dogfood",
            self.dogfood_root / "substrate-dogfood" / "pkgs",
            self.dogfood_root / "website-dogfood" / "pkgs",
        )
        for platform in platform_roots:
            for package in ALL_PLATFORM_PACKAGES:
                (platform / "packages" / package).mkdir(parents=True, exist_ok=True)
                (platform / "packages" / package / "fkst.toml").write_text(
                    f'kind = "package"\nname = "{package}"\n',
                    encoding="utf-8",
                )
            (platform / "scripts").mkdir(parents=True, exist_ok=True)
            shutil.copy2(host_fixture_path("scripts", "run.sh"), platform / "scripts" / "run.sh")
            shutil.copy2(host_fixture_path("scripts", "local_iteration_result.sh"), platform / "scripts" / "local_iteration_result.sh")
            shutil.copy2(host_fixture_path("scripts", "run_bin.sh"), platform / "scripts" / "run_bin.sh")
            shutil.copy2(host_fixture_path("scripts", "test_affected.sh"), platform / "scripts" / "test_affected.sh")
            shutil.copy2(host_fixture_path("scripts", "test_parallel.sh"), platform / "scripts" / "test_parallel.sh")
            shutil.copy2(host_fixture_path("scripts", "test_deadline.sh"), platform / "scripts" / "test_deadline.sh")
            shutil.copy2(host_fixture_path("scripts", "run_department.sh"), platform / "scripts" / "run_department.sh")
            shutil.copy2(REPO_ROOT / "host" / "host_entry.sh", platform / "scripts" / "host_entry.sh")
            shutil.copy2(REPO_ROOT / "host" / "host_run.sh", platform / "scripts" / "host_run.sh")
            shutil.copy2(host_fixture_path("scripts", "composed_manifest.sh"), platform / "scripts" / "composed_manifest.sh")
            shutil.copy2(host_fixture_path("scripts", "composed_conformance.sh"), platform / "scripts" / "composed_conformance.sh")
            shutil.copy2(host_fixture_path("scripts", "check_repo_intake_routing.py"), platform / "scripts" / "check_repo_intake_routing.py")
            shutil.copy2(host_fixture_path("scripts", "intake_policy_slots.json"), platform / "scripts" / "intake_policy_slots.json")
            shutil.copy2(REPO_ROOT / "host" / "bin_bootstrap.sh", platform / "scripts" / "bin_bootstrap.sh")
            shutil.copy2(REPO_ROOT / "host" / "bin_cache.py", platform / "scripts" / "bin_cache.py")
            self.platform_revs[platform] = self._make_platform_git_repo(platform)

        (self.dogfood_root / "website-dogfood" / "site" / ".fkst" / "local-packages" / "site-board").mkdir(
            parents=True,
            exist_ok=True,
        )

        for host, platform in (
            (self.dogfood_root / "pkgs-dogfood", self.dogfood_root / "pkgs-dogfood"),
            (self.dogfood_root / "substrate-dogfood" / "sub", self.dogfood_root / "substrate-dogfood" / "pkgs"),
            (self.dogfood_root / "website-dogfood" / "site", self.dogfood_root / "website-dogfood" / "pkgs"),
        ):
            self._write_host_workspace(host, platform)
        for platform in platform_roots:
            self._advance_platform_git_repo(platform)

    def _write_host_workspace(self, host: Path, platform: Path) -> None:
        if host == platform:
            manifest = "[workspace]\nunits = [\"packages/*\"]\n"
            for package in PLATFORM_PACKAGES.split():
                manifest += (
                    "\n[[package]]\n"
                    f"name = {json.dumps(package)}\n"
                    'source = "workspace"\n'
                    'version = "workspace"\n'
                )
            (host / "fkst.workspace.toml").write_text(manifest, encoding="utf-8")
            return

        packages = PLATFORM_PACKAGES.split()
        if host == self.dogfood_root / "website-dogfood" / "site":
            packages = WEBSITE_PLATFORM_PACKAGES.split()
            if self.stale_website_manifest:
                packages = STALE_WEBSITE_PACKAGES.split()
        (host / "fkst.workspace.toml").write_text(
            textwrap.dedent(
                f"""\
                [workspace]
                units = [".fkst/local-packages/*"]

                [[external_sources]]
                id = "fkst-packages-platform"
                git = {json.dumps(str(platform))}
                packages = {json.dumps(packages)}
                """
            ),
            encoding="utf-8",
        )
        (host / "fkst.lock").write_text(
            textwrap.dedent(
                f"""\
                [[external_source]]
                id = "fkst-packages-platform"
                git = {json.dumps(str(platform))}

                [external_source.resolved]
                rev = {json.dumps(self.platform_revs[platform])}
                tree_sha256 = "sha256-test"
                """
            ),
            encoding="utf-8",
        )

    def _make_platform_git_repo(self, platform: Path) -> str:
        git_env = self._git_env()
        run_git(["init", "-q"], cwd=platform, env=git_env)
        run_git(["add", "."], cwd=platform, env=git_env)
        run_git(["commit", "-q", "-m", "seed"], cwd=platform, env=git_env)
        return git_stdout(["rev-parse", "HEAD"], cwd=platform, env=git_env)

    def _advance_platform_git_repo(self, platform: Path) -> None:
        marker = platform / "packages" / "github-proxy" / "current.txt"
        marker.write_text("advanced local platform checkout\n", encoding="utf-8")
        git_env = self._git_env()
        run_git(["add", "packages/github-proxy/current.txt"], cwd=platform, env=git_env)
        run_git(["commit", "-q", "-m", "advance local platform"], cwd=platform, env=git_env)

    def _git_env(self) -> dict[str, str]:
        git_env = os.environ.copy()
        git_env.update(
            {
                "GIT_AUTHOR_NAME": "Host Run Equivalence",
                "GIT_AUTHOR_EMAIL": "host-run-equivalence@example.invalid",
                "GIT_COMMITTER_NAME": "Host Run Equivalence",
                "GIT_COMMITTER_EMAIL": "host-run-equivalence@example.invalid",
                "GIT_AUTHOR_DATE": "2001-09-09T01:46:40Z",
                "GIT_COMMITTER_DATE": "2001-09-09T01:46:40Z",
            }
        )
        return git_env

    def env(self, target: str) -> dict[str, str]:
        base_path = os.environ.get("PATH", "")
        env = {
            "PATH": f"{self.bin_dir}:{base_path}",
            "DOGFOOD_ROOT": str(self.dogfood_root),
            "DOGFOOD_REPOS": target,
            "DOGFOOD_CONFIG": str(self.root / "missing-config.sh"),
            "SUBSTRATE_SRC": str(self.substrate_src),
            "BIN": str(self.fake_bin),
            "BOT": "test-bot",
            "GH_ORG": "ExampleOrg",
            "UPSTREAM_BRANCH": "dev",
            "INTEGRATION_BRANCH": "integration-test",
            "ROLLUP_MERGE": "auto",
            "MANAGED_BOT_LOGINS": "test-bot,peer-bot",
            "RATE_POOL": str(self.dogfood_root / "rate-pools"),
            "LOGDIR": str(self.dogfood_root),
            "CAPTURE_FILE": str(self.capture),
            "FKST_NO_AUTOBUILD": "1",
            "FKST_GITHUB_WRITE": "0",
            "FKST_DEVLOOP_LOCAL_TEST_COMMAND": "true",
            "DUR_PACKAGES": str(self.dogfood_root / "stable-durable-packages"),
            "DUR_SUBSTRATE": str(self.dogfood_root / "stable-durable-substrate"),
            "DUR_WEBSITE": str(self.dogfood_root / "stable-durable-website"),
        }
        env["DOGFOOD_REPOS"] = target
        return env

    def launch(self, target: str) -> dict[str, object]:
        self.capture.unlink(missing_ok=True)
        result = run_bounded(
            [str(self.script), "start", target],
            cwd=self.root,
            env=self.env(target),
        )
        if result.returncode != 0:
            raise AssertionError(
                f"dogfood start {target} failed with {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.capture.exists():
                return json.loads(self.capture.read_text(encoding="utf-8"))
            time.sleep(0.05)
        raise AssertionError(f"dogfood start {target} did not invoke fake supervise\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    def run_start(self, target: str) -> subprocess.CompletedProcess[str]:
        self.capture.unlink(missing_ok=True)
        return run_bounded(
            [str(self.script), "start", target],
            cwd=self.root,
            env=self.env(target),
        )

    def run_sync(self, target: str) -> subprocess.CompletedProcess[str]:
        self.capture.unlink(missing_ok=True)
        return run_bounded(
            [str(self.script), "sync", target],
            cwd=self.root,
            env=self.env(target),
        )


def load_golden_launches() -> dict[str, object]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def normalize(record: dict[str, object], root: Path) -> dict[str, object]:
    root_markers = sorted({str(root), str(root.resolve())}, key=len, reverse=True)

    def norm(value: object) -> object:
        if isinstance(value, str):
            for marker in root_markers:
                value = value.replace(marker, "$ROOT")
            return value
        if isinstance(value, list):
            return [norm(item) for item in value]
        if isinstance(value, dict):
            return {key: norm(item) for key, item in value.items()}
        return value

    return norm(record)  # type: ignore[return-value]


class HostRunEquivalenceTest(unittest.TestCase):
    maxDiff = None

    def test_external_commands_cannot_bypass_bounded_runner(self) -> None:
        self.assertNotIn("subprocess" + ".run(", Path(__file__).read_text(encoding="utf-8"))

    def test_bounded_runner_kills_process_tree_on_timeout_and_parent_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "child-tree.sh"
            write_executable(
                script,
                "#!/usr/bin/env bash\nsleep 60 >/dev/null 2>&1 &\nprintf '%s %s\\n' \"$$\" \"$!\" > \"$1\"\n[ \"$2\" != timeout ] || wait\n",
            )
            for mode in ("timeout", "success"):
                pid_file = root / f"{mode}.pids"
                pids: list[int] = []
                started_at = time.monotonic()
                try:
                    if mode == "timeout":
                        # Deadline must comfortably exceed the fixture's pid-file write, else under load the
                        # tree is killed before line writes "$1" and the read below FileNotFoundErrors (flake
                        # observed 2026-07-22: 1.0s raced the write under contention). 3.0s stays well under
                        # the <5.0s bound below while giving the sub-second write ample scheduling margin.
                        with self.assertRaises(subprocess.TimeoutExpired):
                            run_bounded(
                                [str(script), str(pid_file), mode],
                                cwd=root,
                                env=os.environ.copy(),
                                timeout=3.0,
                            )
                        self.assertLess(time.monotonic() - started_at, 8.0)
                    else:
                        result = run_bounded(
                            [str(script), str(pid_file), mode], cwd=root, env=os.environ.copy(), timeout=5.0
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                    pids = [int(value) for value in pid_file.read_text(encoding="utf-8").split()]
                    self.assertEqual(len(pids), 2)
                    for pid in pids:
                        self.assertTrue(wait_for_process_exit(pid), f"process {pid} survived {mode} cleanup")
                finally:
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    @REQUIRES_HOST_FIXTURE
    def test_delegated_dogfood_launch_matches_committed_golden_for_all_targets(self) -> None:
        golden = load_golden_launches()
        self.assertEqual(set(golden), set(TARGETS))
        new_script = host_fixture_path(".claude", "skills", "dogfood-github-devloop", "dogfood.sh").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            new_layout = DogfoodLayout(
                tmp_root / "new",
                new_script,
            )

            for target in TARGETS:
                with self.subTest(target=target):
                    new_record = normalize(new_layout.launch(target), new_layout.root)
                    self.assertEqual(new_record, golden[target])
                    env = new_record["env"]  # type: ignore[index]
                    self.assertEqual(env["FKST_GITHUB_WRITE"], "1")  # type: ignore[index]
                    self.assertEqual(env["FKST_DEVLOOP_LOCAL_TEST_COMMAND"], "true")  # type: ignore[index]
                    self.assertEqual(
                        env["FKST_RUNTIME_ROOT"],  # type: ignore[index]
                        f"$ROOT/dogfood/dogfood-rt-{target}.{FIXED_TS}",
                    )
                    hydrated_roots = {
                        "substrate": new_layout.dogfood_root
                        / "substrate-dogfood"
                        / "sub"
                        / ".fkst"
                        / "run"
                        / "fkst-packages-platform",
                        "website": new_layout.dogfood_root
                        / "website-dogfood"
                        / "site"
                        / ".fkst"
                        / "run"
                        / "fkst-packages-platform",
                    }
                    if target in hydrated_roots:
                        self.assertFalse(hydrated_roots[target].exists())

    @REQUIRES_HOST_FIXTURE
    def test_website_start_uses_manifest_without_rewriting_it(self) -> None:
        new_script = (DOGFOOD_FIXTURE_ROOT / "dogfood.sh").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = DogfoodLayout(
                Path(tmp) / "stale-website",
                new_script,
                stale_website_manifest=True,
            )

            result = layout.run_start("website")

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(layout.capture.exists())
            workspace = layout.dogfood_root / "website-dogfood" / "site" / "fkst.workspace.toml"
            self.assertIn(
                f"packages = {json.dumps(STALE_WEBSITE_PACKAGES.split())}",
                workspace.read_text(encoding="utf-8"),
            )
            self.assertNotIn("fkst-substrate-ref-maintainer", workspace.read_text(encoding="utf-8"))
            self.assertNotIn("integration-coverage-producer", workspace.read_text(encoding="utf-8"))
            capture = json.loads(layout.capture.read_text(encoding="utf-8"))
            argv = " ".join(capture["argv"])
            self.assertNotIn("github-devloop-intake", argv)
            self.assertNotIn("github-ratchet-migration-slicer", argv)

    @REQUIRES_HOST_FIXTURE
    def test_non_self_host_without_platform_source_fails_before_launch(self) -> None:
        new_script = (DOGFOOD_FIXTURE_ROOT / "dogfood.sh").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = DogfoodLayout(
                Path(tmp) / "missing-platform",
                new_script,
            )
            workspace = layout.dogfood_root / "website-dogfood" / "site" / "fkst.workspace.toml"
            workspace.write_text('[workspace]\nunits = [".fkst/local-packages/*"]\n', encoding="utf-8")

            result = layout.run_start("website")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "target fkst.workspace.toml must declare external_sources(id=fkst-packages-platform)",
                result.stderr + result.stdout,
            )
            self.assertFalse(layout.capture.exists())

    @REQUIRES_HOST_FIXTURE
    def test_dogfood_start_fails_when_supervise_exits_before_readiness(self) -> None:
        new_script = (DOGFOOD_FIXTURE_ROOT / "dogfood.sh").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = DogfoodLayout(
                Path(tmp) / "failed",
                new_script,
            )
            write_executable(
                layout.fake_bin,
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import os
                    from pathlib import Path

                    Path(os.environ["CAPTURE_FILE"]).write_text("launched\\n", encoding="utf-8")
                    print("startup error: schema validation failed", flush=True)
                    raise SystemExit(17)
                    """
                ),
            )

            result = layout.run_start("packages")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAILED to start", result.stdout)
            self.assertIn("startup error: schema validation failed", result.stdout)

    @REQUIRES_HOST_FIXTURE
    def test_dogfood_sync_fails_when_selective_auto_restart_exits_before_readiness(self) -> None:
        new_script = (DOGFOOD_FIXTURE_ROOT / "dogfood.sh").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = DogfoodLayout(
                Path(tmp) / "sync-failed",
                new_script,
            )
            (layout.dogfood_root / "stable-durable-packages").mkdir(parents=True, exist_ok=True)
            (layout.dogfood_root / "stable-durable-packages" / ".fkst-supervise.pid").write_text(
                "999999\n",
                encoding="utf-8",
            )
            (layout.dogfood_root / "packages-sv-100.log").write_text(
                "TIMESTAMP=2026-01-01T00:00:00Z LEVEL=info EVENT=code_provenance "
                "ENGINE_VER=aaaaaaaa PKG_VERS=github-devloop@bbbbbbbb\n",
                encoding="utf-8",
            )
            write_executable(layout.bin_dir / "pgrep", "#!/usr/bin/env bash\nprintf '999999\\n'\n")
            write_executable(
                layout.bin_dir / "git",
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    cdir=""
                    if [ "${1:-}" = "-C" ]; then
                      cdir="$2"
                      shift 2
                    fi
                    cmd="${1:-}"
                    case "$cmd" in
                      rev-parse)
                        case "${2:-}" in
                          --is-inside-work-tree) printf 'true\\n' ;;
                          --git-dir) printf '.git\\n' ;;
                          --show-toplevel) printf '%s\\n' "${cdir:-$PWD}" ;;
                          --verify) exit 0 ;;
                          --short) printf 'aaaaaaaa\\n' ;;
                          origin/*|HEAD) printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\n' ;;
                          *) printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\n' ;;
                        esac
                        ;;
                      fetch|status|merge-base|checkout|merge|push|reset) exit 0 ;;
                      rev-list) printf '0\\n' ;;
                      diff) printf 'changed package\\n' ;;
                      worktree) exit 0 ;;
                      *) exit 0 ;;
                    esac
                    """
                ),
            )
            write_executable(
                layout.fake_bin,
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import os
                    from pathlib import Path

                    Path(os.environ["CAPTURE_FILE"]).write_text("launched\\n", encoding="utf-8")
                    print("startup error: schema validation failed", flush=True)
                    raise SystemExit(17)
                    """
                ),
            )

            result = layout.run_sync("packages")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("packages: pkg-stale -> auto-restart", result.stdout)
            self.assertIn("FAILED to start", result.stdout)
            self.assertIn("startup error: schema validation failed", result.stdout)

    @REQUIRES_HOST_FIXTURE
    def test_manifest_based_launch_keeps_workspace_byte_stable(self) -> None:
        new_script = (DOGFOOD_FIXTURE_ROOT / "dogfood.sh").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = DogfoodLayout(
                Path(tmp) / "byte-stable",
                new_script,
            )
            workspace = layout.dogfood_root / "website-dogfood" / "site" / "fkst.workspace.toml"
            packages = WEBSITE_PLATFORM_PACKAGES.split()
            committed_style = textwrap.dedent(
                f"""\
                [workspace]
                units = [".fkst/local-packages/*"]

                [[external_sources]]
                id = "fkst-packages-platform"
                git = {json.dumps(str(layout.dogfood_root / "website-dogfood" / "pkgs"))}
                packages = [
                {''.join(f'  {json.dumps(package)},\n' for package in packages)}]
                """
            )
            workspace.write_text(committed_style, encoding="utf-8")

            result = layout.run_start("website")

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(workspace.read_text(encoding="utf-8"), committed_style)

    @REQUIRES_HOST_FIXTURE
    def test_sync_restores_generated_workspace_scratch_before_forward_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._git_env()
            script = self._copy_dogfood_skill(root)
            dogfood_root = root / "dogfood"
            pkgs = dogfood_root / "substrate-dogfood" / "pkgs"
            host = dogfood_root / "substrate-dogfood" / "sub"
            packages_remote = self._create_branch_remote(root, "packages-remote", {"README.md": "packages\n"})
            host_remote = self._create_host_remote(root, "host-remote")
            self._clone_branch(packages_remote, pkgs, env)
            self._clone_branch(host_remote, host, env)
            base_text = self._base_workspace_text(root / "platform")
            (host / "fkst.workspace.toml").write_text(
                self._generated_workspace_text(root / "platform", PLATFORM_PACKAGES.split()),
                encoding="utf-8",
            )
            self.assertNotEqual((host / "fkst.workspace.toml").read_text(encoding="utf-8"), base_text)

            result = self._run_dogfood_sync(script, root, "substrate")

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("merged + pushed", result.stdout)
            self.assertNotIn("does not merge cleanly", result.stdout)
            dev_head = git_stdout(["rev-parse", "origin/dev"], cwd=host, env=env)
            integration_head = git_stdout(["rev-parse", "origin/integration-test"], cwd=host, env=env)
            self.assertEqual(integration_head, dev_head)
            self.assertEqual(git_stdout(["status", "--porcelain"], cwd=host, env=env), "")

    @REQUIRES_HOST_FIXTURE
    def test_sync_conflict_remains_for_real_workspace_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._git_env()
            script = self._copy_dogfood_skill(root)
            dogfood_root = root / "dogfood"
            pkgs = dogfood_root / "substrate-dogfood" / "pkgs"
            host = dogfood_root / "substrate-dogfood" / "sub"
            packages_remote = self._create_branch_remote(root, "packages-remote", {"README.md": "packages\n"})
            host_remote = self._create_host_remote(root, "host-remote")
            self._clone_branch(packages_remote, pkgs, env)
            self._clone_branch(host_remote, host, env)
            real_edit = self._base_workspace_text(root / "platform").replace(
                'id = "fkst-packages-platform"\n',
                'id = "fkst-packages-platform"\nrev = "human-edit"\n',
            )
            (host / "fkst.workspace.toml").write_text(real_edit, encoding="utf-8")

            result = self._run_dogfood_sync(script, root, "substrate")

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("does not merge cleanly", result.stdout)
            dev_head = git_stdout(["rev-parse", "origin/dev"], cwd=host, env=env)
            integration_head = git_stdout(["rev-parse", "origin/integration-test"], cwd=host, env=env)
            self.assertNotEqual(integration_head, dev_head)

    def _git_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "Dogfood Sync Test",
                "GIT_AUTHOR_EMAIL": "dogfood-sync-test@example.invalid",
                "GIT_COMMITTER_NAME": "Dogfood Sync Test",
                "GIT_COMMITTER_EMAIL": "dogfood-sync-test@example.invalid",
                "GIT_AUTHOR_DATE": "2001-09-09T01:46:40Z",
                "GIT_COMMITTER_DATE": "2001-09-09T01:46:40Z",
            }
        )
        return env

    def _copy_dogfood_skill(self, root: Path) -> Path:
        skill_dir = root / "skill"
        skill_dir.mkdir()
        script = skill_dir / "dogfood.sh"
        shutil.copy2(DOGFOOD_FIXTURE_ROOT / "dogfood.sh", script)
        script.chmod(0o755)
        shutil.copy2(
            DOGFOOD_FIXTURE_ROOT / "workspace_manifest.py",
            skill_dir / "workspace_manifest.py",
        )
        return script

    def _run_dogfood_sync(self, script: Path, root: Path, target: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "DOGFOOD_ROOT": str(root / "dogfood"),
                "DOGFOOD_REPOS": target,
                "DOGFOOD_CONFIG": str(root / "missing-config.sh"),
                "SUBSTRATE_SRC": str(root / "not-a-substrate-checkout"),
                "BIN": str(root / "missing-framework"),
                "BOT": "test-bot",
                "GH_ORG": "ExampleOrg",
                "UPSTREAM_BRANCH": "dev",
                "INTEGRATION_BRANCH": "integration-test",
                "FKST_DEVLOOP_UPSTREAM_BRANCH": "dev",
                "FKST_DEVLOOP_INTEGRATION_BRANCH": "integration-test",
                "ROLLUP_MERGE": "auto",
                "RATE_POOL": str(root / "rate-pools"),
                "LOGDIR": str(root / "dogfood"),
            }
        )
        return run_bounded(
            [str(script), "sync", target],
            cwd=root,
            env=env,
        )

    def _create_branch_remote(self, root: Path, name: str, files: dict[str, str]) -> Path:
        env = self._git_env()
        source = root / f"{name}-source"
        source.mkdir()
        run_git(["init", "-q"], cwd=source, env=env)
        for rel, content in files.items():
            path = source / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        run_git(["add", "."], cwd=source, env=env)
        run_git(["commit", "-q", "-m", "seed"], cwd=source, env=env)
        run_git(["branch", "dev"], cwd=source, env=env)
        run_git(["branch", "integration-test"], cwd=source, env=env)
        remote = root / name
        run_git(["init", "--bare", "-q", str(remote)], cwd=root, env=env)
        run_git(["remote", "add", "origin", str(remote)], cwd=source, env=env)
        run_git(["push", "-q", "origin", "dev", "integration-test"], cwd=source, env=env)
        return remote

    def _create_host_remote(self, root: Path, name: str) -> Path:
        env = self._git_env()
        source = root / f"{name}-source"
        source.mkdir()
        run_git(["init", "-q"], cwd=source, env=env)
        (root / "platform").mkdir()
        (source / "fkst.workspace.toml").write_text(self._base_workspace_text(root / "platform"), encoding="utf-8")
        run_git(["add", "fkst.workspace.toml"], cwd=source, env=env)
        run_git(["commit", "-q", "-m", "integration base"], cwd=source, env=env)
        run_git(["branch", "integration-test"], cwd=source, env=env)
        (source / "fkst.workspace.toml").write_text(self._dev_workspace_text(root / "platform"), encoding="utf-8")
        run_git(["add", "fkst.workspace.toml"], cwd=source, env=env)
        run_git(["commit", "-q", "-m", "advance dev workspace"], cwd=source, env=env)
        run_git(["branch", "dev"], cwd=source, env=env)
        remote = root / name
        run_git(["init", "--bare", "-q", str(remote)], cwd=root, env=env)
        run_git(["remote", "add", "origin", str(remote)], cwd=source, env=env)
        run_git(["push", "-q", "origin", "dev", "integration-test"], cwd=source, env=env)
        return remote

    def _clone_branch(self, remote: Path, checkout: Path, env: dict[str, str]) -> None:
        checkout.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", "-q", "--branch", "integration-test", str(remote), str(checkout)], cwd=checkout.parent, env=env)

    def _base_workspace_text(self, platform: Path) -> str:
        return textwrap.dedent(
            f"""\
            [workspace]
            units = []

            [[external_sources]]
            id = "fkst-packages-platform"
            git = {json.dumps(str(platform))}
            packages = [
              "github-devloop",
              "github-proxy",
            ]
            """
        )

    def _generated_workspace_text(self, platform: Path, packages: list[str] | None = None) -> str:
        packages = packages or ["github-devloop", "github-proxy"]
        return textwrap.dedent(
            f"""\
            [workspace]
            units = []

            [[external_sources]]
            id = "fkst-packages-platform"
            git = {json.dumps(str(platform))}
            packages = {json.dumps(packages)}
            """
        )

    def _dev_workspace_text(self, platform: Path) -> str:
        return textwrap.dedent(
            f"""\
            [workspace]
            units = ["packages/*"]

            [[external_sources]]
            id = "fkst-packages-platform"
            git = {json.dumps(str(platform))}
            packages = [
              "github-devloop",
              "github-proxy",
            ]
            """
        )


if __name__ == "__main__":
    unittest.main()
