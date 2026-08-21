#!/usr/bin/env python3
"""Focused checks for the declaration-backed operator lift."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
LAUNCH_ENVIRONMENT = ROOT / "ops" / "deployment_launch_environment.sh"
FKST_OPS = ROOT / "bin" / "fkst-ops"
DOCTOR = ROOT / "doctor" / "doctor.sh"
MANIFEST = ROOT / "ops" / "workspace_manifest.py"

class OperatorLiftTest(unittest.TestCase):
    def test_mechanism_tools_have_no_runtime_path_lookup(self) -> None:
        source = OPERATOR.read_text(encoding="utf-8")
        self.assertNotIn("type -P gh", source)
        self.assertNotIn("type -P gh-app", source)
        self.assertNotIn("command -v lsof", source)
        self.assertNotIn("writer_census=$(lsof ", source)

    def test_carried_lsof_runs_with_restricted_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / "platform"
            runtime = root / "runtime"
            logs = root / "logs"
            old_runtime = runtime / "fixture.old"
            platform.mkdir()
            old_runtime.mkdir(parents=True)
            logs.mkdir()
            subprocess.run(["git", "-C", str(platform), "init", "-q"], check=True)
            lsof = root / "carried-lsof"
            lsof.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
            lsof.chmod(0o755)
            command = f'''PYTHON="{sys.executable}"
_self_dir="{ROOT / 'ops'}"
eval "$(sed -n '/^clean_stale_runtime_worktrees()/,/^}}/p' "{OPERATOR}")"
PKGSRC="$1"; RUNTIME_ROOT="$2"; LOGDIR="$3"; LSOF="$4"
clean_stale_runtime_worktrees fixture "$2/fixture.current"
'''
            result = subprocess.run(
                ["/bin/bash", "-c", command, "test", str(platform), str(runtime),
                 str(logs), str(lsof)],
                env={**os.environ, "PATH": "/usr/bin:/bin"},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse(old_runtime.exists())

    def test_stop_all_preserves_an_earlier_target_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            resolved = {
                "deployment": [
                    {
                        "id": name,
                        "target_identity": name,
                        "managed_bot_logins": ["bot"],
                        "machine": {
                            "target_checkout": f"/{name}",
                            "platform_checkout": f"/{name}",
                            "engine_checkout": "/engine",
                            "engine_binary": "/engine/bin",
                            "durable": f"/{name}/durable",
                            "runtime": f"/{name}/runtime",
                            "logs": f"/{name}/logs",
                        },
                        "github_devloop_profile": {},
                        "engine_revision": {"path": "control/ref"},
                        "providers": {
                            key: {
                                "executable": "/provider",
                                "contract": "v1",
                                "configuration": {"build_command": ["/bin/true"]}
                                if key == "engine" else {},
                            }
                            for key in (
                                "github_credential", "engine", "board_engine_durable",
                                "board_github_control",
                            )
                        },
                        "claim_posture": {"mode": "label", "label_exclusive": False},
                        "author_authorization": {
                            "authorized_logins": [],
                            "authorize_org_members": False,
                            "authorize_repo_collaborators": False,
                        },
                        "integration": {
                            "upstream_branch": "dev",
                            "integration_branch": "integration",
                            "rollup_merge": "merge",
                        },
                        "github_write_enabled": False,
                        "packages": {"host": []},
                        "sources": {
                            "target": {"git": "target"},
                            "platform": {"git": "platform"}, "engine": {"git": "engine"},
                        },
                    }
                    for name in ("fails", "stopped")
                ]
            }
            resolved_fixture = tools / "resolved.json"
            resolved_fixture.write_text(json.dumps(resolved), encoding="ascii")
            (tools / "machine.toml").write_text(
                '[tools]\ncodex = "/usr/bin/true"\ngh = "/usr/bin/true"\n'
                'gh-app = "/usr/bin/true"\n',
                encoding="ascii",
            )
            fake_python = tools / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = -P ]; then shift; fi\n"
                "if [ \"$1\" = -m ] && [ \"$2\" = schema.validator ]; then\n"
                "  exec cat \"$RESOLVED_FIXTURE\"\n"
                "else\n"
                f"  exec {sys.executable} \"$@\"\n"
                "fi\n",
                encoding="ascii",
            )
            fake_python.chmod(0o755)
            fake_pgrep = tools / "pgrep"
            fake_pgrep.write_text(
                "#!/bin/sh\ncase \"$*\" in *'/fails '*) echo 999999999;; esac\n",
                encoding="ascii",
            )
            fake_pgrep.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{tools}:{os.environ['PATH']}",
                "FKST_OPS_DECLARATION": str(tools / "deployment.toml"),
                "FKST_OPS_MACHINE_PROFILE": str(tools / "machine.toml"),
                "FKST_OPS_LOCK": str(tools / "fkst.lock"),
                "RESOLVED_FIXTURE": str(resolved_fixture),
            }

            result = subprocess.run(
                ["bash", str(OPERATOR), "stop", "all"],
                env=environment, text=True, capture_output=True, check=False,
            )

            self.assertEqual(1, result.returncode, result.stdout + result.stderr)
            self.assertEqual("[stopped] not running\n", result.stdout, result.stderr)
            self.assertEqual("[fails] failed to SIGKILL 999999999\n", result.stderr)

    def test_stop_reports_running_stopped_unknown_and_kill_failure_honestly(self) -> None:
        stop_function = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^stop_one()/,/^}}/p' "{OPERATOR}")"
cfg() {{ [ "$1" != unknown ] || {{ echo "unknown deployment: $1" >&2; return 1; }}; }}
pidof_df() {{ printf '%s' "${{FAKE_PID:-}}"; }}
kill() {{ [ "${{KILL_FAIL:-0}}" = 0 ]; }}
stop_one "$1"
'''
        cases = (
            ("running", {"FAKE_PID": "4321"}, 0, "[running] killed 4321 with SIGKILL\n", ""),
            ("stopped", {}, 0, "[stopped] not running\n", ""),
            ("unknown", {}, 1, "", "unknown deployment: unknown\n"),
            ("running", {"FAKE_PID": "4321", "KILL_FAIL": "1"}, 1, "", "[running] failed to SIGKILL 4321\n"),
        )
        for name, extra_env, status, stdout, stderr in cases:
            with self.subTest(name=name, extra_env=extra_env):
                result = subprocess.run(
                    ["bash", "-c", stop_function, "test", name],
                    env={**os.environ, **extra_env}, text=True, capture_output=True, check=False,
                )
                self.assertEqual(status, result.returncode)
                self.assertEqual(stdout, result.stdout)
                self.assertEqual(stderr, result.stderr)

    def test_two_runtime_configs_receive_the_same_resolved_declaration_roster(self) -> None:
        roster = ["bot-a", "bot-b"]

        def deployment(name: str, actor: str) -> dict[str, object]:
            providers = {
                field: {
                    "executable": "/provider",
                    "contract": "v1",
                    "configuration": {"build_command": ["/bin/true"]}
                    if field == "engine" else {},
                }
                for field in (
                    "github_credential", "engine", "board_engine_durable",
                    "board_github_control",
                )
            }
            return {
                "id": name,
                "target_identity": f"example/{name}",
                "managed_bot_logins": roster,
                "machine": {
                    "target_checkout": f"/{name}/target",
                    "platform_checkout": f"/{name}/platform",
                    "engine_checkout": "/engine",
                    "engine_binary": "/engine/bin",
                    "durable": f"/{name}/durable",
                    "runtime": f"/{name}/runtime",
                    "logs": f"/{name}/logs",
                    "bot_login": actor,
                },
                "github_devloop_profile": {},
                "engine_revision": {"path": "control/ref"},
                "providers": providers,
                "claim_posture": {"mode": "label", "label_exclusive": False},
                "author_authorization": {
                    "authorized_logins": [],
                    "authorize_org_members": False,
                    "authorize_repo_collaborators": False,
                },
                "integration": {
                    "upstream_branch": "dev",
                    "integration_branch": "integration",
                    "rollup_merge": "enabled",
                },
                "github_write_enabled": False,
                "packages": {"host": []},
                "sources": {
                    "target": {"git": "target"},
                    "platform": {"git": "platform"}, "engine": {"git": "engine"},
                },
            }

        with tempfile.TemporaryDirectory() as directory:
            resolved = Path(directory) / "resolved.json"
            resolved.write_text(json.dumps({
                "deployment": [deployment("runtime-a", "bot-a"), deployment("runtime-b", "bot-b")]
            }), encoding="ascii")
            command = f'''PYTHON="{sys.executable}"
RESOLVED_DECLARATION="$(cat "$1")"
eval "$(sed -n '/^cfg()/,/^}}/p' "{OPERATOR}")"
for name in runtime-a runtime-b; do
  cfg "$name" || exit
  printf '%s\t%s\n' "$BOT" "$MANAGED_BOT_LOGINS"
done
'''
            result = subprocess.run(
                ["/bin/bash", "-c", command, "test", str(resolved)],
                text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ['bot-a\t["bot-a","bot-b"]', 'bot-b\t["bot-a","bot-b"]'],
        )

    def test_sync_to_run_branch_propagates_fetch_and_checkout_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            fake_git = tools / "git"
            fake_git.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = -C ] && shift 2\n"
                "case \"$MODE:$*\" in\n"
                "  fetch:rev-parse*--git-dir*) exit 0;;\n"
                "  fetch:fetch*) exit 9;;\n"
                "  checkout:rev-parse*--git-dir*) exit 0;;\n"
                "  checkout:fetch*) exit 0;;\n"
                "  checkout:rev-parse*origin/integration*) echo abc; exit 0;;\n"
                "  checkout:checkout*) exit 8;;\n"
                "  checkout:reset*) exit 8;;\n"
                "  checkout:clean*) exit 0;;\n"
                "  checkout:rev-parse*HEAD*) echo abc; exit 0;;\n"
                "esac\n"
                "exit 1\n",
                encoding="ascii",
            )
            fake_git.chmod(0o755)
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^sync_to_run_branch()/,/^}}/p' "{OPERATOR}")"
INTEGRATION_BRANCH=integration
sync_to_run_branch /checkout
'''
            for mode, marker, status in (
                ("fetch", "FETCH-FAILED", 1), ("checkout", "CHECKOUT-FAILED", 8)
            ):
                environment = {**os.environ, "MODE": mode, "PATH": f"{tools}:{os.environ['PATH']}"}
                result = subprocess.run(
                    ["bash", "-c", command], env=environment, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertIn(marker, result.stdout)

    def test_pinned_source_is_verified_without_advancing_to_integration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            (source / "state").write_text("pinned\n", encoding="ascii")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "pinned"], check=True)
            pinned = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(source), "branch", "integration"], check=True)
            checkout = root / "checkout"
            subprocess.run(["git", "clone", "-q", str(source), str(checkout)], check=True)
            (source / "state").write_text("advanced\n", encoding="ascii")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "advance integration"], check=True)
            subprocess.run(["git", "-C", str(source), "branch", "-f", "integration", "HEAD"], check=True)
            advanced = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "integration"], text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(checkout), "fetch", "-q", "origin", "integration"], check=True)
            subprocess.run(
                ["git", "-C", str(checkout), "checkout", "-q", "-B", "integration", "origin/integration"],
                check=True,
            )
            self.assertEqual(advanced, subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip())
            tree = subprocess.run(
                [sys.executable, str(ROOT / "bootstrap" / "canonical_tree.py"), str(source), pinned],
                text=True, capture_output=True, check=True,
            ).stdout.strip()
            command = f'''PYTHON="{sys.executable}"
_repo_root="{ROOT}"
_self_dir="{ROOT / 'ops'}"
eval "$(sed -n '/^restore_generated_workspace_scratch()/,/^}}/p' "{OPERATOR}")"
eval "$(sed -n '/^sync_to_pinned_revision()/,/^}}/p' "{OPERATOR}")"
sync_to_pinned_revision "$1" "$2" "$3"
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), pinned, tree],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(pinned, subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip())
            self.assertEqual("", subprocess.run(
                ["git", "-C", str(checkout), "branch", "--show-current"], text=True,
                capture_output=True, check=True,
            ).stdout.strip())
            self.assertNotEqual(pinned, advanced)
            self.assertIn("(pinned", result.stdout)

    def test_pinned_source_failure_is_typed_and_does_not_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
            (source / "state").write_text("branch\n", encoding="ascii")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "branch"], check=True)
            checkout = root / "checkout"
            subprocess.run(["git", "clone", "-q", str(source), str(checkout)], check=True)
            before = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip()
            missing = "f" * 40
            command = f'''PYTHON="{sys.executable}"
_repo_root="{ROOT}"
_self_dir="{ROOT / 'ops'}"
eval "$(sed -n '/^restore_generated_workspace_scratch()/,/^}}/p' "{OPERATOR}")"
eval "$(sed -n '/^sync_to_pinned_revision()/,/^}}/p' "{OPERATOR}")"
sync_to_pinned_revision "$1" "$2" "sha256-{'0' * 64}"
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), missing],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("PINNED-FETCH-FAILED", result.stdout)
            self.assertEqual(before, subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True,
                capture_output=True, check=True,
            ).stdout.strip())

    def _capture_launch_environment(self, write: str | None,
        credential_source: str = "github-app",
        advance_platform_before_spawn: bool = False,
    ) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / "platform"
            platform.mkdir()
            (platform / "packages" / "pkg").mkdir(parents=True)
            (platform / "packages" / "pkg" / "fkst.toml").write_text(
                'kind = "package"\nname = "pkg"\n', encoding="ascii"
            )
            engine_script = root / "fixture-engine"
            revision_path = platform / ".control" / "engine-ref"
            revision_path.parent.mkdir(parents=True)
            selected_engine_revision = "b" * 40
            revision_path.write_text(selected_engine_revision + "\n", encoding="ascii")
            capture = root / "capture.json"
            helper = root / "credential-helper"
            identity_proof = (
                "target-access-only;bot-login-not-mechanically-proven"
                if credential_source == "github-app"
                else "login-verified;token-scope-account-wide-not-repository-scoped"
            )
            helper.write_text(
                "#!/bin/sh\nprintf '%s\\n' '"
                + json.dumps({
                    "login": "resolved-bot", "token": "fixture-secret-token",
                    "target": "example/repo", "identity_proof": identity_proof,
                }, separators=(",", ":"))
                + "'\n",
                encoding="ascii",
            )
            helper.chmod(0o755)
            codex_directory = root / "codex-bin"
            codex_directory.mkdir()
            codex = codex_directory / "codex"
            codex.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            codex.chmod(0o755)
            machine_profile = root / "machine.toml"
            machine_profile.write_text(
                f'[tools]\ngh = "/usr/bin/true"\ngh-app = "/usr/bin/true"\n'
                f'codex = "{codex}"\n',
                encoding="ascii",
            )
            resolved_fixture = root / "resolved.json"
            resolved_fixture.write_text('{"deployment": []}\n', encoding="ascii")
            fake_python = root / "python"
            fake_python.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = -P ]; then shift; fi\n'
                'if [ "$1" = -m ] && [ "$2" = schema.validator ]; then\n'
                '  exec cat "$RESOLVED_FIXTURE"\n'
                "fi\n"
                f'exec "{sys.executable}" "$@"\n',
                encoding="ascii",
            )
            fake_python.chmod(0o755)
            engine_script.write_text(
                f"#!{sys.executable}\n"
                "import json, os, pathlib, shutil, subprocess, sys, time\n"
                "keys = ['PATH', 'FKST_CARGO', 'FKST_PYTHON', 'FKST_ENGINE_SOURCE_GIT', 'FKST_GITHUB_CREDENTIAL_SOURCE', 'FKST_GITHUB_CREDENTIAL_RESOLVER', 'FKST_GITHUB_REAL_GH', 'FKST_GITHUB_WRITE', 'FKST_GITHUB_CLAIM_MODE', 'FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE', 'FKST_RATE_POOL_ROOT', 'FKST_GITHUB_BOT_LOGIN', 'FKST_DEVLOOP_MANAGED_BOT_LOGINS', 'FKST_GITHUB_AUTHORIZED_LOGINS', 'FKST_GITHUB_AUTHORIZE_ORG_MEMBERS', 'FKST_GITHUB_AUTHORIZE_REPO_COLLABORATORS', 'FKST_EXPECTED_ENGINE_REVISION', 'FKST_PROJECT_ROOT', 'FKST_RUNTIME_ROOT', 'FKST_DURABLE_ROOT', 'FKST_CODEX_REPOSITORY_ROOTS']\n"
                "captured = {key: os.environ.get(key) for key in keys}\n"
                "package_root = pathlib.Path(sys.argv[sys.argv.index('--package-root') + 1])\n"
                "platform_root = package_root.parents[1]\n"
                "captured['platform_root'] = str(platform_root)\n"
                "captured['platform_revision'] = subprocess.check_output(['git', '-C', str(platform_root), 'rev-parse', 'HEAD'], text=True).strip()\n"
                "captured['engine_revision'] = subprocess.check_output(['git', '-C', str(platform_root), 'show', 'HEAD:.control/engine-ref'], text=True).strip()\n"
                "captured['argv'] = sys.argv\n"
                "captured['pid'], captured['pgid'] = os.getpid(), os.getpgid(0)\n"
                "captured['codex'] = shutil.which('codex')\n"
                "captured['python3'], captured['python3_executable'] = shutil.which('python3'), sys.executable\n"
                "open(os.environ['CAPTURE'], 'w').write(json.dumps(captured))\n"
                "print('EVENT=code_provenance ENGINE_VER=test PKG_VERS=pkg@test', flush=True)\n"
                "print('MSG=event runtime running', flush=True)\n"
                # The stub must outlive the operator's post-readiness stability window by a margin
                # that does not depend on machine speed. It is reaped explicitly below, so this is a
                # backstop against a leaked process, NOT the mechanism that ends it. A short sleep
                # here raced `wait_supervise_ready`, whose 30 iterations each spawn ps/awk/grep/sleep:
                # 3.39s idle on a developer machine against a 4s stub, and over 4s on a loaded CI
                # runner, where the stub exited first and the launch was reported as a failed start.
                "time.sleep(300)\n",
                encoding="ascii",
            )
            engine_script.chmod(0o755)
            subprocess.run(["git", "init", "-q", str(platform)], check=True)
            subprocess.run(
                ["git", "-C", str(platform), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(platform), "config", "user.name", "Test"], check=True,
            )
            subprocess.run(["git", "-C", str(platform), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(platform), "commit", "-qm", "selected platform"], check=True,
            )
            selected_platform_revision = subprocess.run(
                ["git", "-C", str(platform), "rev-parse", "HEAD"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            host = root / "host"
            host.mkdir()
            (host / "fkst.workspace.toml").write_text(
                f'[[external_sources]]\nid = "platform"\ngit = {json.dumps(str(platform))}\npackages = ["pkg"]\n',
                encoding="ascii",
            )
            (host / "fkst.lock").write_text(
                f'[[external_source]]\nid = "platform"\ngit = {json.dumps(str(platform))}\n'
                f'[external_source.resolved]\nrev = "{selected_platform_revision}"\ntree_sha256 = "sha256-test"\n',
                encoding="ascii",
            )
            advanced_platform_revision = ""
            if advance_platform_before_spawn:
                (platform / "platform-state").write_text("advanced\n", encoding="ascii")
                subprocess.run(["git", "-C", str(platform), "add", "."], check=True)
                subprocess.run(
                    ["git", "-C", str(platform), "commit", "-qm", "advanced platform"],
                    check=True,
                )
                advanced_platform_revision = subprocess.run(
                    ["git", "-C", str(platform), "rev-parse", "HEAD"],
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                subprocess.run(
                    ["git", "-C", str(platform), "reset", "--hard", "-q", selected_platform_revision],
                    check=True,
                )
            command = f'''source "{OPERATOR}"
cfg() {{ :; }}
ensure_engine_binary_current() {{
  PLATFORM_REVISION={selected_platform_revision}; ENGINE_REVISION={selected_engine_revision}
  ENGINE_BINARY_BASE="$HOST/engine"; BIN="$ENGINE_BINARY_BASE-$ENGINE_REVISION"
  cp "$(dirname "$HOST")/fixture-engine" "$BIN"; chmod 755 "$BIN"
  PYTHONPATH="$_repo_root" "$PYTHON" -c 'import sys; from pathlib import Path; from ops.revision_derivation import write_build_receipt; write_build_receipt(Path(sys.argv[1]), sys.argv[2], ["fixture-build"])' "$BIN" "$ENGINE_REVISION"
  if [ -n "${{RACE_PLATFORM_REVISION:-}}" ]; then
    git -C "$PKGSRC" reset --hard -q "$RACE_PLATFORM_REVISION" || return 1
  fi
}}
engine_build_receipt_current() {{ :; }}
derive_devloop_pkgs_from_workspace() {{ DEVLOOP_PKGS=pkg; }}
clean_stale_runtime_worktrees() {{ :; }}
clean_stale_launch_platforms() {{ :; }}
clean_stale_engine_artifacts() {{ :; }}
engine_panic_count() {{ echo 0; }}
REPO=example/repo; HOST="$1/host"; PKGSRC="$1/platform"; BIN=/bin/true
REVISION_SOURCE="$PKGSRC"; ENGINE_REVISION_PATH=.control/engine-ref
CARGO=/fixture/resolved/cargo
DEPLOYMENT_PYTHON="$FKST_OPS_PYTHON"
DUR="$1/durable"; RUNTIME_ROOT="$1/runtime"; LOGDIR="$1/logs"
RATE_POOL="$1/rates"; BOT=resolved-bot; MANAGED_BOT_LOGINS='["resolved-bot","peer-bot"]'
AUTHORIZED_LOGINS='["trusted-author","second-author"]'; AUTHORIZE_ORG_MEMBERS=1; AUTHORIZE_REPO_COLLABORATORS=0
UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration; ROLLUP_MERGE=enabled
CLAIM_MODE=label; CLAIM_LABEL_EXCLUSIVE=0
LOCAL_PKGS=; ENGINE_GIT_URL=https://github.com/Example-Org/engine-core.git
GITHUB_DEVLOOP_PROFILE='{{}}'; GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION='{json.dumps({"source": credential_source}, separators=(",", ":"))}'
mkdir -p "$HOST" "$DUR" "$RUNTIME_ROOT" "$LOGDIR"
launch_one fixture 0
'''
            ambient_only = root / "ambient-only"
            ambient_only.mkdir()
            env = os.environ.copy()
            env["PATH"] = os.pathsep.join((str(ambient_only), env["PATH"]))
            env["CAPTURE"] = str(capture)
            env["FKST_OPS_DECLARATION"] = str(root / "declaration.toml")
            env["FKST_OPS_LOCK"] = str(root / "fkst.lock")
            env["FKST_OPS_MACHINE_PROFILE"] = str(machine_profile)
            env["FKST_OPS_PYTHON"] = str(fake_python)
            env["GITHUB_CREDENTIAL_PROVIDER"] = str(helper)
            env["FKST_GITHUB_REAL_GH"] = "/usr/bin/true"
            env["FKST_GITHUB_CREDENTIAL_RESOLVER"] = "/usr/bin/true"
            env["RESOLVED_FIXTURE"] = str(resolved_fixture)
            env["RACE_PLATFORM_REVISION"] = advanced_platform_revision
            env.pop("FKST_GITHUB_WRITE", None)
            command = command.replace(
                "CLAIM_MODE=label;", f"GITHUB_WRITE_POSTURE={write or '0'}; CLAIM_MODE=label;"
            )
            try:
                # 60s is a backstop against a pathological hang, not a budget the launch is
                # expected to approach: the readiness wait alone takes over 3s of ps/grep/sleep
                # iterations, and that cost scales with how loaded the machine is.
                result = subprocess.run(
                    ["bash", "-c", command, "test", str(root)],
                    env=env, text=True, capture_output=True, check=False, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                captured = json.loads(capture.read_text(encoding="utf-8"))
                launch_log = next((root / "logs").glob("fixture-sv-*.log"))
                marker = next(
                    field for field in launch_log.read_text(encoding="utf-8").split()
                    if field.startswith("LAUNCH_ENV_SHA256=")
                )
                captured["launch_environment_sha256"] = marker.partition("=")[2]
                captured["selected_platform_revision"] = selected_platform_revision
                return captured
            finally:
                self._reap_launched_stub(capture)

    @staticmethod
    def _reap_launched_stub(capture: Path) -> None:
        """Kill the stub engine launched by the fixture, whatever the test outcome.

        `launch_child.py` calls `os.setsid()`, so the stub owns a process group that is never this
        interpreter's. The guard below still refuses to signal our own group, because a fixture that
        could kill the test runner is worse than a leaked stub.
        """
        try:
            group = int(json.loads(capture.read_text(encoding="utf-8"))["pgid"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if group in (0, os.getpgid(0)):
            return
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def test_sync_never_touches_hydrated_mechanism_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pinned = Path(directory) / ".fkst" / "run" / "fkst-ops" / "checkouts" / ("a" * 40)
            (pinned / "ops").mkdir(parents=True)
            head = pinned / "HEAD"
            head.write_text("a" * 40, encoding="ascii")
            command = f'''set -uo pipefail
eval "$(sed -n '/^cmd_sync()/,/^}}/p' "{OPERATOR}")"
expand() {{ printf 'deployment-a\\n'; }}
cfg() {{
  UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
  HOST=/target; PKGSRC=/platform; RUNTIME_ROOT=/runtime; ENGINE_CHECKOUT=/engine; BIN=/engine/bin
  ENGINE_PROVIDER=/provider; ENGINE_CONTRACT=contract; ENGINE_PROVIDER_CONFIGURATION='{{}}'
}}
git_lock_sweep() {{ :; }}
derive_devloop_pkgs_from_workspace() {{ :; }}
ensure_integration_caught_up() {{ :; }}
sync_to_run_branch() {{ printf 'synced:%s\\n' "$1"; }}
bin_ensure_fresh() {{ echo built; }}
_proc_stale() {{ echo current; }}
restart_one() {{ echo unexpected-restart; return 1; }}
cmd_sync all
'''
            result = subprocess.run(
                ["bash", "-c", command], text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("a" * 40, head.read_text(encoding="ascii"))
            self.assertNotIn("operator checkouts", result.stdout)
            self.assertIn("synced:/platform", result.stdout)
            self.assertIn("synced:/target", result.stdout)
            self.assertNotIn("unexpected-restart", result.stdout)

    def test_engine_provider_receives_only_the_derived_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "engine"
            provider = root / "provider.py"
            provider.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "request=json.load(sys.stdin); value=request['input']\n"
                "assert set(value) == {'engine_checkout','engine_binary','expected_revision','operation','build_command'}\n"
                "assert value['expected_revision'] == os.environ['EXPECTED_REVISION']\n"
                "binary=pathlib.Path(value['engine_binary']); binary.write_text('#!/bin/sh\\n'); binary.chmod(0o755)\n"
                "json.dump({'version':'fkst.ops.invocation.v1','ok':True,'result':{'binary':str(binary),'source_rev':value['expected_revision']}},sys.stdout)\n",
                encoding="ascii",
            )
            provider.chmod(0o755)
            revision = "a" * 40
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
_self_dir="{ROOT / 'ops'}"
invoke_provider() {{ python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }}
eval "$(sed -n '/^invoke_engine_build_provider()/,/^}}/p' "{OPERATOR}")"
ENGINE_CHECKOUT=/engine-checkout; BIN="$2"; ENGINE_REVISION="$3"
ENGINE_PROVIDER="$1"; ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION='{{"build_command":["true"]}}'
invoke_engine_build_provider
'''
            env = {**os.environ, "EXPECTED_REVISION": revision}
            result = subprocess.run(
                ["bash", "-c", command, "test", str(provider), str(binary), revision],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["binary"], str(binary))
            self.assertTrue(binary.is_file())
            self.assertTrue(os.access(binary, os.X_OK))
            self.assertEqual(response["result"]["source_rev"], revision)

    def test_engine_provider_failure_is_visible_through_deployment_operator_caller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = Path(directory) / "provider"
            provider.write_text(
                "#!/bin/sh\n"
                "cat >/dev/null\n"
                "printf '%s\\n' '{\"version\":\"fkst.ops.invocation.v1\",\"ok\":false,\"failure\":{\"code\":\"REVISION_MISMATCH\",\"message\":\"expected derived revision aaaa, found bbbb\",\"details\":{}}}'\n"
                "exit 1\n",
                encoding="ascii",
            )
            provider.chmod(0o755)
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
_self_dir="{ROOT / 'ops'}"
invoke_provider() {{ python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }}
eval "$(sed -n '/^invoke_engine_build_provider()/,/^}}/p' "{OPERATOR}")"
ENGINE_CHECKOUT=/engine; BIN=/engine/bin; ENGINE_REVISION={'a' * 40}
ENGINE_PROVIDER="$1"; ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION='{{"build_command":["true"]}}'
invoke_engine_build_provider
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(provider)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["failure"]["code"], "REVISION_MISMATCH")
            self.assertIn("fkst.ops.engine.v1", result.stderr)
            self.assertIn("REVISION_MISMATCH", result.stderr)
            self.assertIn("expected derived revision", result.stderr)

    def test_shell_is_valid_and_uses_schema_validator(self) -> None:
        subprocess.run(["bash", "-n", str(OPERATOR)], check=True)
        subprocess.run(["bash", "-n", str(FKST_OPS)], check=True)
        source = OPERATOR.read_text(encoding="utf-8")
        entry_source = FKST_OPS.read_text(encoding="utf-8")
        doctor_source = DOCTOR.read_text(encoding="utf-8")
        self.assertIn('\"$PYTHON\" -P -m schema.validator', source)
        self.assertIn('\"$PYTHON\" -P -c \'', source)
        self.assertIn('\"$PYTHON\" -P -m schema.validator', entry_source)
        self.assertIn('\"$DOCTOR_PYTHON\" -P -m schema.validator', doctor_source)
        for shell_source in (source, entry_source):
            self.assertEqual(
                [line for line in shell_source.splitlines() if "python3" in line],
                ['PYTHON="${FKST_OPS_PYTHON:-python3}"'],
            )
        self.assertIn('rt="$RUNTIME_ROOT/${name}.${ts}"', source)
        self.assertNotIn("GH_ORG=", source)
        self.assertNotIn("GITHUB_PROXY_POLL_LABEL_PREFIX=", source)
        self.assertNotIn("  doctor)", source)
        self.assertIn("ensure_engine_binary_current || return 1", source)
        self.assertNotIn("engine_build_result", source)
        self.assertNotIn("expected_branch", source)
        self.assertNotIn("assert_shared_engine_revision", source)
        self.assertNotIn("SHARED_ENGINE_REVISION_CONFLICT", source)
        self.assertIn('engine_build_receipt_current || {', source)
        self.assertIn('FKST_EXPECTED_ENGINE_REVISION="$ENGINE_REVISION" "$PYTHON" "$_repo_root/board/board.py"', source)
        self.assertIn('assert_engine_pair "$PLATFORM_REVISION" "$ENGINE_REVISION" || return 1', source)
        self.assertNotIn('GH_TOKEN="$GITHUB_TOKEN_DISCOVERED"', source)
        self.assertIn('FKST_GITHUB_REAL_GH="$REAL_GH"', source)

    def test_restart_without_operator_environment_reproduces_declared_write_posture(self) -> None:
        command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^github_write_posture()/,/^}}/p' "{OPERATOR}")"
github_write_posture
'''
        absent = os.environ.copy()
        absent.pop("FKST_GITHUB_WRITE", None)
        enabled = subprocess.run(
            ["bash", "-c", "GITHUB_WRITE_POSTURE=1\n" + command], env=absent,
            text=True, capture_output=True, check=False,
        )
        missing = subprocess.run(["bash", "-c", command], env=absent, text=True, capture_output=True, check=False)
        ambient_opposite = subprocess.run(
            ["bash", "-c", "GITHUB_WRITE_POSTURE=1\n" + command],
            env={**absent, "FKST_GITHUB_WRITE": "0"}, text=True, capture_output=True, check=False,
        )
        self.assertEqual((enabled.returncode, enabled.stdout), (0, "1\n"))
        self.assertEqual((ambient_opposite.returncode, ambient_opposite.stdout), (0, "1\n"))
        self.assertNotEqual(missing.returncode, 0)

        self.assertEqual(self._capture_launch_environment(None)["FKST_GITHUB_WRITE"], "0")
        self.assertEqual(self._capture_launch_environment("1")["FKST_GITHUB_WRITE"], "1")

    def test_launch_exports_resolved_machine_values_and_declaration_roster(self) -> None:
        source = OPERATOR.read_text(encoding="utf-8")
        launch = source[source.index("launch_one() {") : source.index("launch_with_lock_retry() {")]
        environment = LAUNCH_ENVIRONMENT.read_text(encoding="utf-8")
        expected = {
            'FKST_RATE_POOL_ROOT="$RATE_POOL"': "rate_pool",
            'FKST_GITHUB_BOT_LOGIN="$BOT"': "bot_login",
            'FKST_DEVLOOP_MANAGED_BOT_LOGINS="$DEPLOYMENT_CHILD_MANAGED_BOT_LOGINS"': "managed_bot_logins",
        }
        for export, machine_value in expected.items():
            with self.subTest(machine_value=machine_value):
                self.assertIn(export, environment)
        self.assertIn('"${DEPLOYMENT_CHILD_ENVIRONMENT[@]}"', launch)
        self.assertNotIn("FKST_OPS_PROFILE_MACHINE=", environment)
        captured = self._capture_launch_environment(None)
        self.assertTrue(captured["FKST_RATE_POOL_ROOT"].endswith("/rates"))
        self.assertEqual(captured["FKST_GITHUB_BOT_LOGIN"], "resolved-bot")
        self.assertEqual(captured["FKST_DEVLOOP_MANAGED_BOT_LOGINS"], "resolved-bot,peer-bot")
        self.assertEqual(captured["FKST_GITHUB_REAL_GH"], "/usr/bin/true")
        self.assertEqual(captured["FKST_GITHUB_CREDENTIAL_SOURCE"], "github-app")
        self.assertEqual(captured["FKST_GITHUB_CREDENTIAL_RESOLVER"], "/usr/bin/true")
        self.assertEqual(captured["FKST_ENGINE_SOURCE_GIT"], "https://github.com/Example-Org/engine-core.git")

    def test_launch_records_environment_contract_identity(self) -> None:
        identity = self._capture_launch_environment(None)["launch_environment_sha256"]
        self.assertEqual(len(identity), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in identity))

    def test_launch_uses_the_verified_pair_when_platform_advances_before_spawn(self) -> None:
        captured = self._capture_launch_environment(None, advance_platform_before_spawn=True)

        self.assertEqual(captured["selected_platform_revision"], captured["platform_revision"])
        self.assertEqual("b" * 40, captured["engine_revision"])
        self.assertIn("/runtime/.platform/", captured["platform_root"])
        self.assertTrue(captured["platform_root"].endswith(captured["selected_platform_revision"]))
        self.assertEqual(captured["pid"], captured["pgid"])
        self.assertEqual(captured["argv"][1], "supervise")

    def test_launch_forwards_resolved_github_credential_source(self) -> None:
        captured = self._capture_launch_environment(
            None, credential_source="github-cli-user"
        )

        self.assertEqual(captured["FKST_GITHUB_CREDENTIAL_SOURCE"], "github-cli-user")

    def test_declared_author_authorization_reaches_launched_process(self) -> None:
        captured = self._capture_launch_environment(None)
        self.assertEqual(captured["FKST_GITHUB_AUTHORIZED_LOGINS"], "trusted-author,second-author")
        self.assertEqual(captured["FKST_GITHUB_AUTHORIZE_ORG_MEMBERS"], "1")
        self.assertEqual(captured["FKST_GITHUB_AUTHORIZE_REPO_COLLABORATORS"], "0")

    def test_declared_claim_posture_reaches_launched_process(self) -> None:
        captured = self._capture_launch_environment(None)
        self.assertEqual(captured["FKST_GITHUB_CLAIM_MODE"], "label")
        self.assertEqual(captured["FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE"], "0")

    def test_resolved_cargo_reaches_launched_process(self) -> None:
        captured = self._capture_launch_environment(None)
        self.assertEqual(captured["FKST_CARGO"], "/fixture/resolved/cargo")

    def test_resolved_python_reaches_launched_process(self) -> None:
        captured = self._capture_launch_environment(None)
        self.assertTrue(captured["FKST_PYTHON"].endswith("/python"))

    def test_bare_python_fallback_resolves_the_executable_it_actually_runs(self) -> None:
        function = next(
            line for line in OPERATOR.read_text(encoding="utf-8").splitlines()
            if line.startswith("resolve_deployment_python()")
        )
        command = f'''PYTHON=python3
{function}
resolve_deployment_python
'''
        result = subprocess.run(
            ["bash", "-c", command], text=True, capture_output=True, check=False,
        )
        expected = subprocess.run(
            ["python3", "-c", "import sys; print(sys.executable)"],
            text=True, capture_output=True, check=True,
        ).stdout
        self.assertEqual((result.returncode, result.stdout), (0, expected))

    def test_launch_fails_closed_when_token_identity_differs_from_declared_bot(self) -> None:
        command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^authorize_github_writer()/,/^}}/p' "{OPERATOR}")"
_self_dir="{ROOT / 'ops'}"
BOT=declared-bot; REPO=example/repo; GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION='{{"source":"github-app"}}'
authorize_github_writer
'''
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "helper"
            helper.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"login":"human-writer","token":"fixture-secret-token","target":"example/repo","identity_proof":"target-access-only;bot-login-not-mechanically-proven"}\'\n', encoding="ascii")
            helper.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", command],
                env={**os.environ, "PATH": "/usr/bin:/bin",
                     "GITHUB_CREDENTIAL_PROVIDER": str(helper),
                     "FKST_GITHUB_REAL_GH": "/usr/bin/true",
                     "FKST_GITHUB_CREDENTIAL_RESOLVER": "/usr/bin/true"},
                text=True, capture_output=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity-mismatch", result.stderr)
        self.assertIn("HEALTH=UNHEALTHY", result.stderr)
        self.assertNotIn("fixture-secret-token", result.stdout + result.stderr)

    def test_gate_admits_matching_app_token_identity(self) -> None:
        command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^authorize_github_writer()/,/^}}/p' "{OPERATOR}")"
_self_dir="{ROOT / 'ops'}"
BOT='fkst-loning-s-macbook-m5[bot]'; REPO=example/repo; GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION='{{"source":"github-app"}}'
authorize_github_writer
'''
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "helper"
            helper.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"login":"fkst-loning-s-macbook-m5[bot]","token":"fixture-secret-token","target":"example/repo","identity_proof":"target-access-only;bot-login-not-mechanically-proven"}\'\n', encoding="ascii")
            helper.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", command],
                env={**os.environ, "PATH": "/usr/bin:/bin",
                     "GITHUB_CREDENTIAL_PROVIDER": str(helper),
                     "FKST_GITHUB_REAL_GH": "/usr/bin/true",
                     "FKST_GITHUB_CREDENTIAL_RESOLVER": "/usr/bin/true"},
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_admits_github_cli_user_without_app_resolver(self) -> None:
        command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^authorize_github_writer()/,/^}}/p' "{OPERATOR}")"
_self_dir="{ROOT / 'ops'}"
BOT=declared-user; REPO=example/repo; GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION='{{"source":"github-cli-user"}}'
authorize_github_writer
'''
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "helper"
            helper.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' \'{"login":"declared-user","token":"fixture-secret-token","target":"example/repo","identity_proof":"login-verified;token-scope-account-wide-not-repository-scoped"}\'\n',
                encoding="ascii",
            )
            helper.chmod(0o755)
            environment = {
                **os.environ,
                "GITHUB_CREDENTIAL_PROVIDER": str(helper),
                "FKST_GITHUB_REAL_GH": "/usr/bin/true",
            }
            environment.pop("FKST_GITHUB_CREDENTIAL_RESOLVER", None)
            result = subprocess.run(
                ["bash", "-c", command], env=environment,
                text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_active_account_resolution_fails_closed_for_ambiguous_report(self) -> None:
        command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^resolve_github_writer()/,/^}}/p' "{OPERATOR}")"
REAL_GH="$1"
resolve_github_writer
'''
        with tempfile.TemporaryDirectory() as directory:
            gh = Path(directory) / "gh"
            gh.write_text("""#!/bin/sh
cat <<'EOF'
github.com
  ✓ Logged in to github.com account first[bot] (GH_TOKEN)
  - Active account: true
  ✓ Logged in to github.com account second[bot] (GH_TOKEN)
  - Active account: true
EOF
""", encoding="utf-8")
            gh.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", command, "test", str(gh)],
                text=True, capture_output=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambiguous or not parseable", result.stderr)

    def test_gate_refuses_missing_refresh_helper(self) -> None:
        command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^authorize_github_writer()/,/^}}/p' "{OPERATOR}")"
BOT=declared-bot
authorize_github_writer
'''
        result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credential-helper-unavailable", result.stderr)
        self.assertIn("HEALTH=UNHEALTHY", result.stderr)

    def test_discovered_token_is_not_logged_reported_or_written_to_artifacts(self) -> None:
        self._capture_launch_environment("1")
        token = "fixture-secret-token"
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "FKST_GITHUB_WRITE=1 FKST_GITHUB_WRITER_LOGIN=resolved-bot "
                "FKST_GITHUB_CLAIM_MODE=label FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=0\n",
                encoding="ascii",
            )
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^status_one()/,/^}}/p' "{OPERATOR}")"
cfg() {{ HOST=/host; PKGSRC=/platform; REPO=example/repo; }}
pidof_df() {{ echo 123; }}; latest_log() {{ echo "$LOG"; }}
fmt_uptime() {{ echo 1m00s; }}; engine_panic_count() {{ echo 0; }}
ps() {{ echo 00:01:00; }}; git() {{ echo abcdef123456; }}
status_one fixture
'''
            result = subprocess.run(
                ["bash", "-c", command], env={**os.environ, "LOG": str(log), "GH_TOKEN": token},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertNotIn(token, log.read_text(encoding="ascii"))
            for artifact in Path(directory).rglob("*"):
                if artifact.is_file():
                    self.assertNotIn(token, artifact.read_text(encoding="utf-8"))

    def test_expired_credential_is_replaced_and_work_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            counter = root / "counter"
            helper = root / "helper"
            helper.write_text(
                f'''#!/bin/sh
n=0; [ ! -f "{counter}" ] || n=$(cat "{counter}"); n=$((n+1)); printf '%s' "$n" > "{counter}"
[ "$n" -eq 1 ] && token=expired || token=fresh
printf '{{"login":"declared-bot","token":"%s","target":"example/repo","identity_proof":"target-access-only;bot-login-not-mechanically-proven"}}\\n' "$token"
''', encoding="ascii",
            )
            helper.chmod(0o755)
            real_gh = root / "real-gh"
            real_gh.write_text(
                '#!/bin/sh\n[ "$GH_TOKEN" = fresh ] || exit 1\nprintf \'work-continued\\n\'\n',
                encoding="ascii",
            )
            real_gh.chmod(0o755)
            env = {
                **os.environ,
                "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
                "FKST_GITHUB_CREDENTIAL_SOURCE": "github-app",
                "FKST_GITHUB_BOT_LOGIN": "declared-bot",
                "FKST_GITHUB_REAL_GH": str(real_gh),
                "FKST_GITHUB_REPO": "example/repo",
            }
            first = subprocess.run(
                [sys.executable, str(ROOT / "ops/github_credential_gh.py"), "api", "/installation"],
                env=env, text=True, capture_output=True, check=False,
            )
            second = subprocess.run(
                [sys.executable, str(ROOT / "ops/github_credential_gh.py"), "api", "/installation"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout, "work-continued\n")
            self.assertEqual(counter.read_text(encoding="ascii"), "2")


if __name__ == "__main__":
    unittest.main()
