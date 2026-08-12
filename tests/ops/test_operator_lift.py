#!/usr/bin/env python3
"""Focused checks for the declaration-backed operator lift."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from schema.mechanism_tools import MECHANISM_TOOLS

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
FKST_OPS = ROOT / "bin" / "fkst-ops"
MANIFEST = ROOT / "ops" / "workspace_manifest.py"

class OperatorLiftTest(unittest.TestCase):
    def test_every_enumerated_mechanism_tool_is_loaded_from_profile(self) -> None:
        source = OPERATOR.read_text(encoding="utf-8")
        loader = source[source.index("MECHANISM_TOOL_ASSIGNMENTS="):
                        source.index("DEPLOYMENT_OPERATOR_DEPLOYMENTS=")]
        self.assertIn("from schema.mechanism_tools import MECHANISM_TOOLS", loader)
        self.assertIn("for name, tool in MECHANISM_TOOLS.items()", loader)
        for name, tool in MECHANISM_TOOLS.items():
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', (ROOT / "schema" / "mechanism_tools.py").read_text())
                if tool.shell_variable is not None:
                    self.assertIn(f'"{tool.shell_variable}"',
                                  (ROOT / "schema" / "mechanism_tools.py").read_text())

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
                            "platform": {"git": "platform"},
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

    def _capture_launch_environment(
        self, write: str | None, deployment_python: str = "/fixture/resolved/python"
    ) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / "platform"
            run_script = platform / "scripts" / "run.sh"
            run_script.parent.mkdir(parents=True)
            capture = root / "capture.json"
            helper = root / "credential-helper"
            helper.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' \'{"login":"resolved-bot","token":"fixture-secret-token","target":"example/repo","identity_proof":"target-access-only;bot-login-not-mechanically-proven"}\'\n',
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
                'if [ "$1" = -m ] && [ "$2" = schema.validator ]; then\n'
                '  exec cat "$RESOLVED_FIXTURE"\n'
                "fi\n"
                f'exec "{sys.executable}" "$@"\n',
                encoding="ascii",
            )
            fake_python.chmod(0o755)
            run_script.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, shutil, time\n"
                "keys = ['PATH', 'FKST_CARGO', 'FKST_PYTHON', 'FKST_GITHUB_CREDENTIAL_RESOLVER', 'FKST_GITHUB_REAL_GH', 'FKST_GITHUB_WRITE', 'FKST_GITHUB_CLAIM_MODE', 'FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE', 'FKST_RATE_POOL_ROOT', 'FKST_GITHUB_BOT_LOGIN', 'FKST_DEVLOOP_MANAGED_BOT_LOGINS', 'FKST_GITHUB_AUTHORIZED_LOGINS', 'FKST_GITHUB_AUTHORIZE_ORG_MEMBERS', 'FKST_GITHUB_AUTHORIZE_REPO_COLLABORATORS']\n"
                "captured = {key: os.environ.get(key) for key in keys}\n"
                "captured['codex'] = shutil.which('codex')\n"
                "open(os.environ['CAPTURE'], 'w').write(json.dumps(captured))\n"
                "print('EVENT=code_provenance ENGINE_VER=test PKG_VERS=pkg@test', flush=True)\n"
                "print('MSG=event runtime running', flush=True)\n"
                "time.sleep(4)\n",
                encoding="ascii",
            )
            run_script.chmod(0o755)
            command = f'''source "{OPERATOR}"
require_engine_binary() {{ :; }}
derive_devloop_pkgs_from_workspace() {{ DEVLOOP_PKGS=pkg; }}
wait_supervise_ready() {{
  local attempts=0
  while [ ! -f "$CAPTURE" ] && [ "$attempts" -lt 50 ]; do sleep 0.1; attempts=$((attempts + 1)); done
  [ -f "$CAPTURE" ]
}}
clean_stale_runtime_worktrees() {{ :; }}
engine_panic_count() {{ echo 0; }}
REPO=example/repo; HOST="$1/host"; PKGSRC="$1/platform"; BIN=/bin/true
CARGO=/fixture/resolved/cargo
DEPLOYMENT_PYTHON={deployment_python!s}
DUR="$1/durable"; RUNTIME_ROOT="$1/runtime"; LOGDIR="$1/logs"
RATE_POOL="$1/rates"; BOT=resolved-bot; MANAGED_BOT_LOGINS='["resolved-bot","peer-bot"]'
AUTHORIZED_LOGINS='["trusted-author","second-author"]'; AUTHORIZE_ORG_MEMBERS=1; AUTHORIZE_REPO_COLLABORATORS=0
UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration; ROLLUP_MERGE=enabled
CLAIM_MODE=label; CLAIM_LABEL_EXCLUSIVE=0
LOCAL_PKGS=; GITHUB_DEVLOOP_PROFILE='{{}}'; GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION='{{"source":"github-app"}}'
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
            env.pop("FKST_GITHUB_WRITE", None)
            command = command.replace(
                "CLAIM_MODE=label;", f"GITHUB_WRITE_POSTURE={write or '0'}; CLAIM_MODE=label;"
            )
            result = subprocess.run(
                ["bash", "-c", command, "test", str(root)],
                env=env, text=True, capture_output=True, check=False, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return json.loads(capture.read_text(encoding="utf-8"))

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
  HOST=/target; PKGSRC=/platform; SUBSTRATE_SRC=/engine; BIN=/engine/bin
  ENGINE_PROVIDER=/provider; ENGINE_CONTRACT=contract; ENGINE_PROVIDER_CONFIGURATION='{{}}'
}}
derive_devloop_pkgs_from_workspace() {{ :; }}
ensure_integration_caught_up() {{ :; }}
bin_ensure_fresh() {{ echo built; }}
_proc_stale() {{ echo current; }}
restart_one() {{ :; }}
cmd_sync all
'''
            result = subprocess.run(
                ["bash", "-c", command], text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("a" * 40, head.read_text(encoding="ascii"))
            self.assertNotIn("operator checkouts", result.stdout)

    def test_engine_provider_uses_deployment_integration_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            (checkout / ".git").mkdir()
            binary = root / "engine"
            self.assertFalse(binary.exists())
            tools = root / "tools"
            tools.mkdir()
            git = tools / "git"
            git.write_text('#!/bin/sh\ncase "$1" in branch) echo build;; pull) :;; rev-parse) printf "%040d\\n" 0;; *) exit 1;; esac\n', encoding="ascii")
            git.chmod(0o755)
            build = root / "build"
            build.write_text('#!/bin/sh\nprintf "#!/bin/sh\\nexit 0\\n" > "$1"\nchmod +x "$1"\n', encoding="ascii")
            build.chmod(0o755)
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
_self_dir="{ROOT / 'ops'}"
invoke_provider() {{ python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }}
eval "$(sed -n '/^engine_build_result()/,/^}}/p' "{OPERATOR}")"
SUBSTRATE_SRC="$1"; BIN="$2"; UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=build
ENGINE_PROVIDER="{ROOT / 'providers/engine.py'}"; ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION="$3"
engine_build_result
'''
            env = os.environ.copy()
            env["PATH"] = f"{tools}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), str(binary),
                 json.dumps({"build_command": [str(build), str(binary)]})],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["binary"], str(binary))
            self.assertTrue(binary.is_file())
            self.assertTrue(os.access(binary, os.X_OK))

    def test_engine_provider_failure_is_visible_through_deployment_operator_caller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = Path(directory) / "provider"
            provider.write_text(
                "#!/bin/sh\n"
                "cat >/dev/null\n"
                "printf '%s\\n' '{\"version\":\"fkst.ops.invocation.v1\",\"ok\":false,\"failure\":{\"code\":\"WRONG_BRANCH\",\"message\":\"expected branch integration, found dev\",\"details\":{}}}'\n"
                "exit 1\n",
                encoding="ascii",
            )
            provider.chmod(0o755)
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
_self_dir="{ROOT / 'ops'}"
invoke_provider() {{ python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }}
eval "$(sed -n '/^engine_build_result()/,/^}}/p' "{OPERATOR}")"
SUBSTRATE_SRC=/engine; BIN=/engine/bin; UPSTREAM_BRANCH=dev; INTEGRATION_BRANCH=integration
ENGINE_PROVIDER="$1"; ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION='{{"build_command":["true"]}}'
bin_ensure_fresh() {{ local response; response=$(engine_build_result) || return $?; }}
bin_ensure_fresh
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(provider)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("fkst.ops.engine.v1", result.stderr)
            self.assertIn("WRONG_BRANCH", result.stderr)
            self.assertIn("expected branch integration, found dev", result.stderr)

    def test_shell_is_valid_and_uses_schema_validator(self) -> None:
        subprocess.run(["bash", "-n", str(OPERATOR)], check=True)
        subprocess.run(["bash", "-n", str(FKST_OPS)], check=True)
        source = OPERATOR.read_text(encoding="utf-8")
        entry_source = FKST_OPS.read_text(encoding="utf-8")
        self.assertIn('\"$PYTHON\" -m schema.validator', source)
        for shell_source in (source, entry_source):
            self.assertEqual(
                [line for line in shell_source.splitlines() if "python3" in line],
                ['PYTHON="${FKST_OPS_PYTHON:-python3}"'],
            )
        self.assertIn('rt="$RUNTIME_ROOT/${name}.${ts}"', source)
        self.assertNotIn("GH_ORG=", source)
        self.assertNotIn("GITHUB_PROXY_POLL_LABEL_PREFIX=", source)
        self.assertNotIn("  doctor)", source)
        self.assertGreaterEqual(source.count("require_engine_binary || return 1"), 3)
        self.assertIn('require_engine_binary || { rm -rf "$tmp"; failed=1; continue; }\n    "$PYTHON" "$_repo_root/board/board.py"', source)
        self.assertIn('require_engine_binary || return 1\n  printf \'FKST_GITHUB_WRITE=', source)
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

    def test_launch_exports_every_resolved_profile_machine_value(self) -> None:
        source = OPERATOR.read_text(encoding="utf-8")
        launch = source[source.index("launch_one() {") : source.index("launch_with_lock_retry() {")]
        expected = {
            'FKST_RATE_POOL_ROOT="$RATE_POOL"': "rate_pool",
            'FKST_GITHUB_BOT_LOGIN="$BOT"': "bot_login",
            'FKST_DEVLOOP_MANAGED_BOT_LOGINS="$managed_bot_logins"': "managed_bot_set",
        }
        for export, machine_value in expected.items():
            with self.subTest(machine_value=machine_value):
                self.assertIn(export, launch)
        self.assertNotIn("FKST_OPS_PROFILE_MACHINE=", launch)
        captured = self._capture_launch_environment(None)
        self.assertTrue(captured["FKST_RATE_POOL_ROOT"].endswith("/rates"))
        self.assertEqual(captured["FKST_GITHUB_BOT_LOGIN"], "resolved-bot")
        self.assertEqual(captured["FKST_DEVLOOP_MANAGED_BOT_LOGINS"], "resolved-bot,peer-bot")
        self.assertEqual(captured["FKST_GITHUB_REAL_GH"], "/usr/bin/true")
        self.assertEqual(captured["FKST_GITHUB_CREDENTIAL_RESOLVER"], "/usr/bin/true")

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

    def test_resolved_python_and_generated_path_reach_launched_process(self) -> None:
        captured = self._capture_launch_environment(None)
        self.assertEqual(captured["FKST_PYTHON"], "/fixture/resolved/python")
        path = captured["PATH"].split(os.pathsep)
        self.assertEqual(path[0], str(ROOT / "ops"))
        self.assertEqual(Path(captured["codex"]), Path(path[1]) / "codex")
        standard_path = os.confstr("CS_PATH") or os.defpath
        self.assertEqual(path[2:], os.get_exec_path({"PATH": standard_path}))
        self.assertNotIn("ambient-only", captured["PATH"])

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

    def test_refreshed_credential_is_secret_and_identity_checked_every_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "never-expose-this-token"
            helper = root / "helper"
            helper.write_text(
                f'''#!/bin/sh
printf '%s\\n' '{{"login":"wrong-bot","token":"{token}"}}'
''', encoding="ascii",
            )
            helper.chmod(0o755)
            args = root / "args"
            real_gh = root / "real-gh"
            real_gh.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{args}"\n', encoding="ascii")
            real_gh.chmod(0o755)
            env = {**os.environ, "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
                   "FKST_GITHUB_BOT_LOGIN": "declared-bot", "FKST_GITHUB_REAL_GH": str(real_gh)}
            result = subprocess.run(
                [sys.executable, str(ROOT / "ops/github_credential_gh.py"), "api", "/repo"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("HEALTH=UNHEALTHY", result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertFalse(args.exists())
            for artifact in root.rglob("*"):
                if artifact.is_file() and artifact not in {helper}:
                    self.assertNotIn(token, artifact.read_text(encoding="utf-8"))

    def test_credential_never_appears_in_real_gh_process_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "process-argument-secret"
            helper = root / "helper"
            helper.write_text(
                f'#!/bin/sh\nprintf \'%s\\n\' \'{{"login":"declared-bot","token":"{token}","target":"example/repo","identity_proof":"target-access-only;bot-login-not-mechanically-proven"}}\'\n',
                encoding="ascii",
            )
            helper.chmod(0o755)
            arguments = root / "arguments"
            real_gh = root / "real-gh"
            real_gh.write_text(
                f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{arguments}"\n', encoding="ascii",
            )
            real_gh.chmod(0o755)
            env = {**os.environ, "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
                   "FKST_GITHUB_BOT_LOGIN": "declared-bot", "FKST_GITHUB_REAL_GH": str(real_gh),
                   "FKST_GITHUB_REPO": "example/repo"}
            result = subprocess.run(
                [sys.executable, str(ROOT / "ops/github_credential_gh.py"), "api", "/repo"],
                env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(arguments.read_text(encoding="ascii"), "api\n/repo\n")
            self.assertNotIn(token, result.stdout + result.stderr + arguments.read_text(encoding="ascii"))

    def test_authentication_failure_is_visible_in_status_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "FKST_GITHUB_WRITE=1 FKST_GITHUB_WRITER_LOGIN=resolved-bot "
                "FKST_GITHUB_CLAIM_MODE=label FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=0\n"
                "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY\n",
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
            result = subprocess.run(["bash", "-c", command], env={**os.environ, "LOG": str(log)},
                                    text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("health=UNHEALTHY", result.stdout)
            self.assertIn("auth-fail=1", result.stdout)

    def test_status_reports_the_running_launch_write_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "supervise.log"
            log.write_text(
                "FKST_GITHUB_WRITE=1 FKST_GITHUB_WRITER_LOGIN=resolved-bot FKST_GITHUB_CLAIM_MODE=label "
                "FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=0\nlast event\n",
                encoding="ascii",
            )
            command = f'''PYTHON="${{FKST_OPS_PYTHON:-python3}}"
eval "$(sed -n '/^status_one()/,/^}}/p' "{OPERATOR}")"
cfg() {{ HOST=/host; PKGSRC=/platform; REPO=example/repo; }}
pidof_df() {{ echo 123; }}
latest_log() {{ echo "$LOG"; }}
fmt_uptime() {{ echo 1m00s; }}
engine_panic_count() {{ echo 0; }}
ps() {{ echo 00:01:00; }}
git() {{ echo abcdef123456; }}
status_one fixture
'''
            result = subprocess.run(
                ["bash", "-c", command], env={**os.environ, "LOG": str(log)},
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("write=1", result.stdout)
            self.assertIn("writer=resolved-bot", result.stdout)
            self.assertIn("claim=label", result.stdout)
            self.assertIn("label-exclusive=0", result.stdout)

    def test_platform_source_role_is_an_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "target-owned-name"\ngit = "ssh://git@example.com/team/platform"\npackages = ["one", "two"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(MANIFEST),
                    "platform-packages",
                    "deployment-a",
                    str(host),
                    str(platform),
                    "https://example.com/team/platform.git",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "one two")

    def test_platform_source_url_zero_matches_fails_closed_with_observed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "other"\ngit = "https://example.com/team/other.git"\npackages = ["one"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python3",
                    str(MANIFEST),
                    "platform-packages",
                    "deployment-a",
                    str(host),
                    str(platform),
                    "https://example.com/team/platform.git",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("https://example.com/team/platform.git", result.stdout)
            self.assertIn("no matches", result.stdout)
            self.assertIn("other", result.stdout)

    def test_platform_source_url_two_matches_fails_closed_with_observed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host"
            platform = root / "platform"
            host.mkdir()
            platform.mkdir()
            (host / "fkst.workspace.toml").write_text(
                '[[external_sources]]\nid = "first"\ngit = "https://example.com/team/platform.git"\npackages = ["one"]\n'
                '[[external_sources]]\nid = "second"\ngit = "git://example.com/team/platform"\npackages = ["two"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                ["python3", str(MANIFEST), "platform-packages", "deployment-a", str(host), str(platform),
                 "ssh://git@example.com/team/platform.git"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("ssh://git@example.com/team/platform.git", result.stdout)
            self.assertIn("2 matches", result.stdout)
            self.assertIn("first", result.stdout)
            self.assertIn("second", result.stdout)

    def test_corrupt_run_checkout_is_recloned_from_resolved_git_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote"
            checkout = root / "run"
            remote.mkdir()
            subprocess.run(["git", "init", "-q", str(remote)], check=True)
            subprocess.run(["git", "-C", str(remote), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(remote), "config", "user.name", "Test"], check=True)
            (remote / "tracked").write_text("restored\n", encoding="ascii")
            subprocess.run(["git", "-C", str(remote), "add", "tracked"], check=True)
            subprocess.run(["git", "-C", str(remote), "commit", "-qm", "fixture"], check=True)
            checkout.mkdir()
            (checkout / "partial").write_text("corrupt\n", encoding="ascii")
            command = f'''set -e
eval "$(sed -n '/^ensure_run_checkout()/,/^}}/p' {OPERATOR})"
ensure_run_checkout "$1" "$2"
'''
            result = subprocess.run(
                ["bash", "-c", command, "test", str(checkout), str(remote)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((checkout / "tracked").read_text(encoding="ascii"), "restored\n")
            self.assertTrue(list(root.glob("run.corrupt.*")))


if __name__ == "__main__":
    unittest.main()
