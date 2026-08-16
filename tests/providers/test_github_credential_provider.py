from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "providers" / "github_credential_gh.py"
TOKEN = "fixture-secret-token"
TARGET = "owner/repo"


def make_tools(root: Path, app_slug: str = "declared-bot") -> tuple[Path, Path, Path]:
    resolver_args = root / "resolver-args"
    resolver = root / "gh-app"
    resolver.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > '{resolver_args}'\n"
        "[ -z \"${GH_TOKEN:-}\" ] && [ -z \"${GITHUB_TOKEN:-}\" ] || exit 42\n"
        f"printf '%s\\n' '{TOKEN}'\n",
        encoding="ascii",
    )
    resolver.chmod(0o755)
    gh = root / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f"[ \"$GH_TOKEN\" = '{TOKEN}' ] || exit 41\n"
        f"if [ \"$1 $2\" = 'api /installation' ]; then printf '%s\\n' '{{\"app_slug\":\"{app_slug}\"}}'; exit 0; fi\n"
        f"if [ \"$1 $3\" = 'api /installation/repositories' ]; then printf '%s\\n' '{TARGET}'; exit 0; fi\n"
        "exit 1\n",
        encoding="ascii",
    )
    gh.chmod(0o755)
    return resolver, gh, resolver_args


def invoke(
    resolver: Path, gh: Path, expected: str, *, source: str = "github-app", **extra: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ, "FKST_GITHUB_CREDENTIAL_RESOLVER": str(resolver),
        "FKST_GITHUB_REAL_GH": str(gh), "FKST_GITHUB_BOT_LOGIN": expected,
        "FKST_GITHUB_REPO": TARGET, "FKST_GITHUB_CREDENTIAL_SOURCE": source,
        **extra,
    }
    return subprocess.run([sys.executable, str(PROVIDER)], env=env, text=True,
                          capture_output=True, check=False)


def make_github_cli_user_gh(
    root: Path, *, login: str = "declared-user", push: str = "true",
    require_github_com_environment: bool = False, fail_call: str = "",
) -> tuple[Path, Path]:
    calls = root / "gh-args"
    gh = root / "gh"
    environment_checks = (
        "[ \"${GH_HOST:-}\" = github.com ] || exit 43\n"
        "[ -z \"${GH_ENTERPRISE_TOKEN:-}\" ] || exit 44\n"
        "[ -z \"${GITHUB_ENTERPRISE_TOKEN:-}\" ] || exit 45\n"
        if require_github_com_environment else ""
    )
    source = (
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" >> '{calls}'\n"
        "[ -z \"${GITHUB_TOKEN:-}\" ] || exit 40\n"
        "if [ \"$1 $2 $3 $4 $5 $6 $7\" = "
        "'auth token --hostname github.com --user declared-user ' ]; then\n"
        "  [ -z \"${GH_TOKEN:-}\" ] || exit 41\n"
        f"  if [ '{fail_call}' = 'mint' ]; then\n"
        f"    printf '%s\\n' '{TOKEN}'\n"
        f"    printf '%s\\n' '{TOKEN}' >&2\n"
        "    exit 46\n"
        "  fi\n"
        f"  printf '%s\\n' '{TOKEN}'\n"
        "  exit 0\n"
        "fi\n"
    )
    source += environment_checks
    source += (
        "[ \"${GH_TOKEN:-}\" = '" + TOKEN + "' ] || exit 42\n"
        "if [ \"$1 $2 $3 $4\" = 'api /user --jq .login' ]; then\n"
        f"  if [ '{fail_call}' = 'user' ]; then\n"
        f"    printf '%s\\n' '{TOKEN}'\n"
        f"    printf '%s\\n' '{TOKEN}' >&2\n"
        "    exit 47\n"
        "  fi\n"
        f"  printf '%s\\n' '{login}'\n"
        "  exit 0\n"
        "fi\n"
        f"if [ \"$1 $2 $3 $4\" = 'api repos/{TARGET} --jq .permissions.push' ]; then\n"
        f"  if [ '{fail_call}' = 'repository' ]; then\n"
        f"    printf '%s\\n' '{TOKEN}'\n"
        f"    printf '%s\\n' '{TOKEN}' >&2\n"
        "    exit 48\n"
        "  fi\n"
        f"  printf '%s\\n' '{push}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    gh.write_text(source, encoding="ascii")
    gh.chmod(0o755)
    return gh, calls


def recorded_arguments(path: Path) -> str:
    return path.read_text(encoding="ascii") if path.exists() else ""


def generated_artifact_contents(root: Path, fixture: Path) -> str:
    return "".join(
        artifact.read_text(encoding="ascii")
        for artifact in root.iterdir()
        if artifact.is_file() and artifact != fixture
    )


def test_installation_without_declared_target_is_refused_without_exposing_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        resolver, gh, _ = make_tools(Path(directory))
        gh.write_text('#!/bin/sh\nexit 1\n', encoding="ascii")
        result = invoke(resolver, gh, "declared-bot[bot]")
    assert result.returncode != 0
    assert "declared-target-not-accessible-to-installation" in result.stderr
    assert TOKEN not in result.stdout + result.stderr


def test_matching_installation_issues_target_bound_json_credential() -> None:
    with tempfile.TemporaryDirectory() as directory:
        resolver, gh, resolver_args = make_tools(Path(directory))
        result = invoke(resolver, gh, "declared-bot[bot]")
        arguments = resolver_args.read_text(encoding="ascii")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "login": "declared-bot[bot]", "token": TOKEN, "target": TARGET,
        "identity_proof": "target-access-only;bot-login-not-mechanically-proven",
    }
    assert arguments == f"token\n--target\n{TARGET}\n"
    assert TOKEN not in arguments


def test_inherited_stale_tokens_cannot_suppress_minting() -> None:
    with tempfile.TemporaryDirectory() as directory:
        resolver, gh, _ = make_tools(Path(directory))
        result = invoke(resolver, gh, "declared-bot[bot]", GH_TOKEN="stale-gh",
                        GITHUB_TOKEN="stale-github")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["token"] == TOKEN
    assert "stale-gh" not in result.stdout + result.stderr
    assert "stale-github" not in result.stdout + result.stderr


def test_token_is_absent_from_output_errors_artifacts_and_arguments() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        resolver, gh, resolver_args = make_tools(root)
        gh.write_text('#!/bin/sh\nexit 1\n', encoding="ascii")
        result = invoke(resolver, gh, "declared-bot[bot]")
        observable = result.stdout + result.stderr + resolver_args.read_text(encoding="ascii")
        for artifact in root.iterdir():
            if artifact.is_file() and artifact not in {resolver, gh}:
                observable += artifact.read_text(encoding="ascii")
    assert result.returncode != 0
    assert TOKEN not in observable


def test_resolver_stderr_reaches_failure_with_command_and_origin() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        resolver, gh, _ = make_tools(root)
        message = "distinctive resolver stderr, unchanged"
        resolver.write_text(f"#!/bin/sh\nprintf '%s' '{message}' >&2\nexit 19\n", encoding="ascii")
        result = invoke(resolver, gh, "declared-bot[bot]")
    assert message in result.stderr
    assert f"command={resolver} token --target {TARGET}" in result.stderr
    match = re.search(r"origin=providers/github_credential_gh.py:(\d+)", result.stderr)
    assert match
    source_lines = PROVIDER.read_text(encoding="utf-8").splitlines()
    assert 'fail("github-app-token-mint-failed"' in source_lines[int(match.group(1)) - 1]


def test_github_cli_user_issues_login_verified_target_writable_credential() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root)
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user",
            source="github-cli-user", GH_TOKEN="stale-gh", GITHUB_TOKEN="stale-github",
        )
        arguments = recorded_arguments(calls)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "login": "declared-user", "token": TOKEN, "target": TARGET,
        "identity_proof": "login-verified;token-scope-account-wide-not-repository-scoped",
    }
    assert arguments == (
        "auth\ntoken\n--hostname\ngithub.com\n--user\ndeclared-user\n"
        "api\n/user\n--jq\n.login\n"
        f"api\nrepos/{TARGET}\n--jq\n.permissions.push\n"
    )
    assert TOKEN not in arguments
    assert "stale-gh" not in result.stdout + result.stderr
    assert "stale-github" not in result.stdout + result.stderr


def test_github_cli_user_mint_failure_does_not_expose_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root, fail_call="mint")
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
        arguments = recorded_arguments(calls)
        artifacts = generated_artifact_contents(root, gh)

    assert result.returncode != 0
    assert "token-mint-failed" in result.stderr
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr
    assert TOKEN not in arguments
    assert TOKEN not in artifacts


def test_github_cli_user_login_failure_does_not_expose_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root, fail_call="user")
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
        arguments = recorded_arguments(calls)
        artifacts = generated_artifact_contents(root, gh)

    assert result.returncode != 0
    assert "login-verification-failed" in result.stderr
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr
    assert TOKEN not in arguments
    assert TOKEN not in artifacts


def test_github_cli_user_repository_failure_does_not_expose_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root, fail_call="repository")
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
        arguments = recorded_arguments(calls)
        artifacts = generated_artifact_contents(root, gh)

    assert result.returncode != 0
    assert "push-verification-failed" in result.stderr
    assert TOKEN not in result.stdout
    assert TOKEN not in result.stderr
    assert TOKEN not in arguments
    assert TOKEN not in artifacts


def test_github_cli_user_refuses_wrong_login_without_exposing_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root, login="different-user")
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
        observable = result.stdout + result.stderr + recorded_arguments(calls)

    assert result.returncode != 0
    assert "login-mismatch" in result.stderr
    assert TOKEN not in observable


def test_github_cli_user_refuses_false_push_permission_without_exposing_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root, push="false")
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
        observable = result.stdout + result.stderr + recorded_arguments(calls)

    assert result.returncode != 0
    assert "push-permission-missing" in result.stderr
    assert TOKEN not in observable


def test_github_cli_user_refuses_malformed_permission_without_exposing_token() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root, push="not-a-boolean")
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
        observable = result.stdout + result.stderr + recorded_arguments(calls)

    assert result.returncode != 0
    assert "push-permission-missing" in result.stderr
    assert TOKEN not in observable


def test_github_cli_user_verification_pins_github_com_credential_environment() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(
            root, require_github_com_environment=True
        )
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user",
            source="github-cli-user", GH_HOST="enterprise.example.invalid",
            GH_ENTERPRISE_TOKEN="hostile-enterprise-token",
            GITHUB_ENTERPRISE_TOKEN="hostile-github-enterprise-token",
        )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["identity_proof"] == (
        "login-verified;token-scope-account-wide-not-repository-scoped"
    )
    assert TOKEN not in recorded_arguments(calls)
