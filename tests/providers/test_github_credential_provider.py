from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
import tempfile
import time
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "providers" / "github_credential_gh.py"
WRAPPER = ROOT / "ops" / "github_credential_gh.py"
TOKEN = "fixture-secret-token"
ROTATED_TOKEN = "fixture-rotated-token"
TARGET = "owner/repo"
ATTESTATION_FIELDS = {
    "schema_version", "source", "login", "target", "token_fingerprint",
    "boot_session_id", "verified_monotonic",
}


def load_provider():
    spec = importlib.util.spec_from_file_location("github_credential_provider_under_test", PROVIDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_wrapper():
    spec = importlib.util.spec_from_file_location("github_credential_wrapper_under_test", WRAPPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROVIDER_MODULE = load_provider()
WRAPPER_MODULE = load_wrapper()


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
    if "FKST_RUNTIME_ROOT" not in extra:
        env.pop("FKST_RUNTIME_ROOT", None)
    return subprocess.run([sys.executable, str(PROVIDER)], env=env, text=True,
                          capture_output=True, check=False)


def make_github_cli_user_gh(
    root: Path, *, login: str = "declared-user", push: str = "true",
    require_github_com_environment: bool = False, fail_call: str = "", token: str = TOKEN,
    mint_signal: Path | None = None, login_started: Path | None = None,
    login_release: Path | None = None, api_status: str = "",
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
        f"if ! printf '%s\\n' \"$@\" >> '{calls}'; then\n"
        "  printf '%s\\n' 'fixture-gh-record-write-failed' >&2\n"
        "  exit 49\n"
        "fi\n"
        "[ -z \"${GITHUB_TOKEN:-}\" ] || exit 40\n"
        "if [ \"$1 $2 $3 $4 $5 $6 $7\" = "
        "'auth token --hostname github.com --user declared-user ' ]; then\n"
        "  [ -z \"${GH_TOKEN:-}\" ] || exit 41\n"
        f"  if [ '{fail_call}' = 'mint' ]; then\n"
        f"    printf '%s\\n' '{token}'\n"
        f"    printf '%s\\n' '{token}' >&2\n"
        "    exit 46\n"
        "  fi\n"
        + (f"  printf x > '{mint_signal}'\n" if mint_signal else "")
        + f"  printf '%s\\n' '{token}'\n"
        "  exit 0\n"
        "fi\n"
    )
    source += environment_checks
    source += (
        "[ \"${GH_TOKEN:-}\" = '" + token + "' ] || exit 42\n"
        + (f"if [ \"$3\" = '-i' ] || [ \"$4\" = '-i' ]; then\n"
           f"  printf '%s\\n' 'HTTP/2.0 {api_status}'\n"
           "  printf '\\n'\n"
           "  exit 1\n"
           "fi\n" if api_status else "")
        + "if [ \"$1 $2 $3 $4\" = 'api /user --jq .login' ]; then\n"
        f"  if [ '{fail_call}' = 'user' ]; then\n"
        f"    printf '%s\\n' '{token}'\n"
        f"    printf '%s\\n' '{token}' >&2\n"
        "    exit 47\n"
        "  fi\n"
        + (f"  printf x > '{login_started}'\n" if login_started else "")
        + (f"  read -r _ < '{login_release}'\n" if login_release else "")
        +
        f"  printf '%s\\n' '{login}'\n"
        "  exit 0\n"
        "fi\n"
        f"if [ \"$1 $2 $3 $4\" = 'api repos/{TARGET} --jq .permissions.push' ]; then\n"
        f"  if [ '{fail_call}' = 'repository' ]; then\n"
        f"    printf '%s\\n' '{token}'\n"
        f"    printf '%s\\n' '{token}' >&2\n"
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


def test_github_cli_user_fixture_recorder_fails_loudly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        gh, calls = make_github_cli_user_gh(Path(directory))
        calls.mkdir()

        result = subprocess.run(
            [gh, "auth", "token", "--hostname", "github.com", "--user", "declared-user"],
            text=True, capture_output=True, check=False,
        )

        assert result.returncode != 0
        assert "fixture-gh-record-write-failed" in result.stderr


def recorded_arguments(path: Path) -> str:
    return path.read_text(encoding="ascii") if path.exists() else ""


def generated_artifact_contents(root: Path, fixture: Path) -> str:
    return "".join(
        artifact.read_text(encoding="ascii")
        for artifact in root.iterdir()
        if artifact.is_file() and artifact != fixture
    )


def provider_environment(root: Path, gh: Path, *, runtime_root: Path | None) -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "FKST_GITHUB_CREDENTIAL_RESOLVER": str(root / "missing-app-resolver"),
        "FKST_GITHUB_REAL_GH": str(gh),
        "FKST_GITHUB_BOT_LOGIN": "declared-user",
        "FKST_GITHUB_REPO": TARGET,
        "FKST_GITHUB_CREDENTIAL_SOURCE": "github-cli-user",
    }
    if runtime_root is not None:
        environment["FKST_RUNTIME_ROOT"] = str(runtime_root)
    return environment


def invoke_provider_at(
    root: Path, gh: Path, *, now: float, runtime_root: Path | None,
    arguments: tuple[str, ...] = (), monotonic_now: float | None = None,
    boot_session_id: str | None = "fixture-boot-session",
) -> subprocess.CompletedProcess[str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    environment = provider_environment(root, gh, runtime_root=runtime_root)
    if monotonic_now is None:
        monotonic_now = now
    with mock.patch.dict(os.environ, environment, clear=True), \
         mock.patch.object(sys, "argv", [str(PROVIDER), *arguments]), \
         mock.patch("time.time", return_value=now), \
         mock.patch("time.clock_gettime", return_value=monotonic_now), \
         mock.patch.object(
             PROVIDER_MODULE, "current_boot_session_id",
             return_value=boot_session_id, create=True,
         ), \
         redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = PROVIDER_MODULE.main()
    return subprocess.CompletedProcess(
        [sys.executable, str(PROVIDER), *arguments], returncode,
        stdout.getvalue(), stderr.getvalue(),
    )


def call_counts(calls: Path) -> tuple[int, int, int]:
    arguments = recorded_arguments(calls).splitlines()
    return arguments.count("auth"), arguments.count("/user"), arguments.count(f"repos/{TARGET}")


def find_attestation(runtime_root: Path) -> tuple[Path, dict[str, object]]:
    matches: list[tuple[Path, dict[str, object]]] = []
    for path in runtime_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and set(document) == ATTESTATION_FIELDS:
            matches.append((path, document))
    assert len(matches) == 1, matches
    return matches[0]


def attestations(runtime_root: Path) -> list[Path]:
    matches: list[Path] = []
    for path in runtime_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and set(document) == ATTESTATION_FIELDS:
            matches.append(path)
    return matches


def assert_token_not_written(root: Path, token: str, *, fixtures: set[Path]) -> None:
    encoded = token.encode("ascii")
    for path in root.rglob("*"):
        if path.is_file() and path not in fixtures:
            assert encoded not in path.read_bytes(), path


def test_installation_repository_probe_nonzero_is_unavailable_without_emitting_credential() -> None:
    with tempfile.TemporaryDirectory() as directory:
        resolver, gh, _ = make_tools(Path(directory))
        message = "distinctive repository probe failure"
        gh.write_text(
            f"#!/bin/sh\nprintf '%s' '{message}' >&2\nexit 23\n", encoding="ascii"
        )
        result = invoke(resolver, gh, "declared-bot[bot]")
    assert result.returncode != 0
    assert "github-app-installation-repositories-verification-unavailable" in result.stderr
    assert message in result.stderr
    assert f"command={gh} api --paginate /installation/repositories" in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert result.stdout == ""

    fact = WRAPPER_MODULE.health_fact(result.stderr)
    assert fact == WRAPPER_MODULE.UNAVAILABLE_FACT
    assert fact != WRAPPER_MODULE.HEALTH_FACT


def test_installation_repository_probe_exception_is_unavailable_without_emitting_credential() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        resolver, gh, _ = make_tools(root)
        message = "distinctive repository probe spawn failure"
        minted = subprocess.CompletedProcess(
            [str(resolver), "token", "--target", TARGET], 0, f"{TOKEN}\n", ""
        )
        environment = {
            "FKST_GITHUB_CREDENTIAL_RESOLVER": str(resolver),
            "FKST_GITHUB_REAL_GH": str(gh),
            "FKST_GITHUB_BOT_LOGIN": "declared-bot[bot]",
            "FKST_GITHUB_REPO": TARGET,
            "FKST_GITHUB_CREDENTIAL_SOURCE": "github-app",
        }
        run_results = iter((minted, OSError(message)))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(
                 PROVIDER_MODULE, "run", side_effect=lambda *args, **kwargs: next(run_results)
             ), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = PROVIDER_MODULE.main()
        result = subprocess.CompletedProcess(
            [sys.executable, str(PROVIDER)], returncode, stdout.getvalue(), stderr.getvalue()
        )

    assert result.returncode != 0
    assert "github-app-installation-repositories-verification-unavailable" in result.stderr
    assert message in result.stderr
    assert f"command={gh} api --paginate /installation/repositories" in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert result.stdout == ""


def test_installation_repository_membership_refusal_does_not_emit_credential() -> None:
    with tempfile.TemporaryDirectory() as directory:
        resolver, gh, _ = make_tools(Path(directory))
        gh.write_text(
            "#!/bin/sh\n"
            f"[ \"$GH_TOKEN\" = '{TOKEN}' ] || exit 41\n"
            "printf '%s\\n' 'owner/another-repository'\n",
            encoding="ascii",
        )
        result = invoke(resolver, gh, "declared-bot[bot]")

    assert result.returncode != 0
    assert "declared-target-not-accessible-to-installation" in result.stderr
    assert "github-app-installation-repositories-verification-unavailable" not in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert result.stdout == ""


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
    assert "login-verification-unavailable" in result.stderr
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
    assert "push-verification-unavailable" in result.stderr
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


def test_github_cli_user_reuses_matching_attestation_but_retrieves_each_time() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(root, gh, now=1_000.0, runtime_root=runtime_root)
        second = invoke_provider_at(root, gh, now=1_001.0, runtime_root=runtime_root)

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        assert json.loads(first.stdout) == json.loads(second.stdout) == {
            "login": "declared-user", "token": TOKEN, "target": TARGET,
            "identity_proof": "login-verified;token-scope-account-wide-not-repository-scoped",
        }
        assert call_counts(calls) == (2, 1, 1)
        state_path, state = find_attestation(runtime_root)
        assert state == {
            "schema_version": 2,
            "source": "github-cli-user",
            "login": "declared-user",
            "target": TARGET,
            "token_fingerprint": hashlib.sha256(TOKEN.encode()).hexdigest(),
            "boot_session_id": "fixture-boot-session",
            "verified_monotonic": 1_000.0,
        }
        assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
        assert all(
            stat.S_IMODE(path.stat().st_mode) == 0o600
            for path in state_path.parent.iterdir() if path.is_file()
        )
        assert TOKEN not in str(state_path)
        assert_token_not_written(root, TOKEN, fixtures={gh})


def test_github_cli_user_attestation_ttl_is_half_open() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(root, gh, now=2_000.0, runtime_root=runtime_root)
        just_below = invoke_provider_at(root, gh, now=2_059.999, runtime_root=runtime_root)
        at_ttl = invoke_provider_at(root, gh, now=2_060.0, runtime_root=runtime_root)

        assert PROVIDER_MODULE.ATTESTATION_TTL_SECONDS == 60
        assert first.returncode == just_below.returncode == at_ttl.returncode == 0
        assert call_counts(calls) == (3, 2, 2)


def test_github_cli_user_partial_wall_clock_rollback_does_not_extend_attestation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(
            root, gh, now=1_000.0, monotonic_now=2_000.0,
            runtime_root=runtime_root,
        )
        before_rollback = invoke_provider_at(
            root, gh, now=1_050.0, monotonic_now=2_050.0,
            runtime_root=runtime_root,
        )
        after_rollback = invoke_provider_at(
            root, gh, now=1_059.0, monotonic_now=2_099.0,
            runtime_root=runtime_root,
        )

        assert first.returncode == before_rollback.returncode == after_rollback.returncode == 0
        assert call_counts(calls) == (3, 2, 2)


def test_github_cli_user_token_change_invalidates_attestation_immediately() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)
        first = invoke_provider_at(root, gh, now=3_000.0, runtime_root=runtime_root)
        _, first_state = find_attestation(runtime_root)

        assert first.returncode == 0, first.stderr
        assert first_state["token_fingerprint"] == hashlib.sha256(TOKEN.encode()).hexdigest()
        assert first_state["verified_monotonic"] == 3_000.0

        gh, calls = make_github_cli_user_gh(root, token=ROTATED_TOKEN)
        second = invoke_provider_at(root, gh, now=3_001.0, runtime_root=runtime_root)
        _, second_state = find_attestation(runtime_root)

        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["token"] == ROTATED_TOKEN
        assert (
            second_state["token_fingerprint"]
            == hashlib.sha256(ROTATED_TOKEN.encode()).hexdigest()
        )
        assert second_state["verified_monotonic"] == 3_001.0
        assert call_counts(calls) == (2, 2, 2)
        assert_token_not_written(root, ROTATED_TOKEN, fixtures={gh})


def test_github_cli_user_attestation_field_mismatch_reverifies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)
        baseline = invoke_provider_at(root, gh, now=4_000.0, runtime_root=runtime_root)
        assert baseline.returncode == 0, baseline.stderr

        for index, (field, value) in enumerate((
            ("source", "github-app"),
            ("login", "different-user"),
            ("target", "different/repository"),
        ), start=1):
            state_path, state = find_attestation(runtime_root)
            state[field] = value
            state_path.write_text(json.dumps(state), encoding="utf-8")
            result = invoke_provider_at(
                root, gh, now=4_000.0 + index, runtime_root=runtime_root
            )
            assert result.returncode == 0, (field, result.stderr)

        assert call_counts(calls) == (4, 4, 4)


def test_github_cli_user_failed_miss_is_not_attested_or_exposed() -> None:
    for fail_call in ("mint", "user", "repository"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "runtime"
            runtime_root.mkdir()
            gh, calls = make_github_cli_user_gh(root, fail_call=fail_call)

            result = invoke_provider_at(root, gh, now=5_000.0, runtime_root=runtime_root)

            assert result.returncode != 0, fail_call
            assert attestations(runtime_root) == []
            assert TOKEN not in result.stdout + result.stderr
            assert TOKEN not in recorded_arguments(calls)
            assert_token_not_written(root, TOKEN, fixtures={gh})


def test_github_cli_user_negative_attestation_age_reverifies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(root, gh, now=6_000.0, runtime_root=runtime_root)
        clock_moved_back = invoke_provider_at(root, gh, now=5_999.0, runtime_root=runtime_root)

        assert first.returncode == clock_moved_back.returncode == 0
        assert call_counts(calls) == (2, 2, 2)


def test_github_cli_user_boot_session_change_reverifies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(
            root, gh, now=6_500.0, monotonic_now=100.0,
            boot_session_id="boot-session-a", runtime_root=runtime_root,
        )
        after_reboot = invoke_provider_at(
            root, gh, now=6_501.0, monotonic_now=101.0,
            boot_session_id="boot-session-b", runtime_root=runtime_root,
        )

        assert first.returncode == after_reboot.returncode == 0
        assert call_counts(calls) == (2, 2, 2)


def test_github_cli_user_unavailable_boot_session_identity_disables_attestation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(
            root, gh, now=6_600.0, monotonic_now=100.0,
            boot_session_id=None, runtime_root=runtime_root,
        )
        second = invoke_provider_at(
            root, gh, now=6_601.0, monotonic_now=101.0,
            boot_session_id=None, runtime_root=runtime_root,
        )

        assert first.returncode == second.returncode == 0
        assert call_counts(calls) == (2, 2, 2)
        assert list(runtime_root.rglob(PROVIDER_MODULE.ATTESTATION_STATE_NAME)) == []


def test_github_cli_user_untrusted_state_shapes_reverify() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)
        baseline = invoke_provider_at(root, gh, now=7_000.0, runtime_root=runtime_root)
        assert baseline.returncode == 0, baseline.stderr

        state_path, state = find_attestation(runtime_root)
        state_path.write_text("{not-json", encoding="ascii")
        malformed = invoke_provider_at(root, gh, now=7_001.0, runtime_root=runtime_root)

        state_path, state = find_attestation(runtime_root)
        state["padding"] = "x" * 1_000_000
        state_path.write_text(json.dumps(state), encoding="utf-8")
        oversized = invoke_provider_at(root, gh, now=7_002.0, runtime_root=runtime_root)

        state_path, state = find_attestation(runtime_root)
        state["schema_version"] = PROVIDER_MODULE.ATTESTATION_SCHEMA_VERSION + 1
        state_path.write_text(json.dumps(state), encoding="utf-8")
        wrong_version = invoke_provider_at(root, gh, now=7_003.0, runtime_root=runtime_root)

        state_path, state = find_attestation(runtime_root)
        state["schema_version"] = True
        state_path.write_text(json.dumps(state), encoding="utf-8")
        malformed_version = invoke_provider_at(
            root, gh, now=7_004.0, runtime_root=runtime_root
        )

        assert (
            malformed.returncode == oversized.returncode == wrong_version.returncode
            == malformed_version.returncode == 0
        )
        assert call_counts(calls) == (5, 5, 5)


def test_github_cli_user_oversized_integer_state_reverifies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)
        baseline = invoke_provider_at(root, gh, now=7_250.0, runtime_root=runtime_root)
        assert baseline.returncode == 0, baseline.stderr

        state_path, state = find_attestation(runtime_root)
        payload = json.dumps(state, separators=(",", ":"))
        schema_member = f'"schema_version":{state["schema_version"]}'
        assert schema_member in payload
        payload = payload.replace(schema_member, '"schema_version":' + "9" * 5_000)
        assert len(payload.encode()) < PROVIDER_MODULE.ATTESTATION_MAX_BYTES
        state_path.write_text(payload, encoding="ascii")
        try:
            result = invoke_provider_at(root, gh, now=7_251.0, runtime_root=runtime_root)
        except ValueError:
            result = None

        assert result is not None, "plain ValueError escaped malformed-state cache-miss handling"
        assert result.returncode == 0, result.stderr
        assert call_counts(calls) == (2, 2, 2)


def test_github_cli_user_deeply_nested_state_reverifies() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)
        baseline = invoke_provider_at(root, gh, now=7_500.0, runtime_root=runtime_root)
        assert baseline.returncode == 0, baseline.stderr

        state_path, _ = find_attestation(runtime_root)
        state_path.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="ascii")
        try:
            result = invoke_provider_at(root, gh, now=7_501.0, runtime_root=runtime_root)
        except RecursionError:
            result = None

        assert result is not None, "deeply nested state escaped cache-miss handling"
        assert result.returncode == 0, result.stderr
        assert call_counts(calls) == (2, 2, 2)


def test_github_cli_user_without_runtime_root_verifies_every_call() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(root, gh, now=8_000.0, runtime_root=None)
        second = invoke_provider_at(root, gh, now=8_001.0, runtime_root=None)

        assert first.returncode == second.returncode == 0
        assert call_counts(calls) == (2, 2, 2)


def test_github_cli_user_unusable_runtime_root_verifies_every_call() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        unusable_runtime_root = root / "not-a-directory"
        unusable_runtime_root.write_text("fixture", encoding="ascii")
        gh, calls = make_github_cli_user_gh(root)

        first = invoke_provider_at(root, gh, now=9_000.0, runtime_root=unusable_runtime_root)
        second = invoke_provider_at(root, gh, now=9_001.0, runtime_root=unusable_runtime_root)

        assert first.returncode == second.returncode == 0
        assert call_counts(calls) == (2, 2, 2)


def test_github_cli_user_auth_check_forces_fresh_verification() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        cached = invoke_provider_at(root, gh, now=10_000.0, runtime_root=runtime_root)
        state_path, _ = find_attestation(runtime_root)
        state_before_auth_check = state_path.read_bytes()
        auth_check = invoke_provider_at(
            root, gh, now=10_001.0, runtime_root=runtime_root,
            arguments=("--fkst-auth-check",),
        )

        assert cached.returncode == auth_check.returncode == 0
        assert call_counts(calls) == (2, 2, 2)
        assert state_path.read_bytes() == state_before_auth_check


def test_github_cli_user_auth_check_does_not_publish_attestation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)

        auth_check = invoke_provider_at(
            root, gh, now=10_500.0, runtime_root=runtime_root,
            arguments=("--fkst-auth-check",),
        )

        assert auth_check.returncode == 0, auth_check.stderr
        assert call_counts(calls) == (1, 1, 1)
        assert list(runtime_root.rglob(PROVIDER_MODULE.ATTESTATION_STATE_NAME)) == []


def test_github_cli_user_wrapper_auth_check_forces_provider_verification() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        gh, calls = make_github_cli_user_gh(root)
        cached = invoke_provider_at(
            root, gh, now=time.time(), runtime_root=runtime_root
        )
        environment = provider_environment(root, gh, runtime_root=runtime_root)
        environment["FKST_GITHUB_CREDENTIAL_HELPER"] = str(PROVIDER)

        auth_check = subprocess.run(
            [sys.executable, str(WRAPPER), "--fkst-auth-check"],
            env=environment, text=True, capture_output=True, check=False,
        )

        assert cached.returncode == 0, cached.stderr
        assert auth_check.returncode == 0, auth_check.stderr
        assert call_counts(calls) == (2, 2, 2)
        assert TOKEN not in auth_check.stdout + auth_check.stderr
        assert TOKEN not in " ".join(auth_check.args)


def test_github_cli_user_verification_is_single_flight() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        runtime_root = root / "runtime"
        runtime_root.mkdir()
        mint_signal = root / "mint-signal"
        login_started = root / "login-started"
        login_release = root / "login-release"
        for fifo in (mint_signal, login_started, login_release):
            os.mkfifo(fifo)
        fifo_flags = os.O_RDWR | os.O_NONBLOCK
        mint_fd = os.open(mint_signal, fifo_flags)
        started_fd = os.open(login_started, fifo_flags)
        release_fd = os.open(login_release, fifo_flags)
        gh, calls = make_github_cli_user_gh(
            root, mint_signal=mint_signal, login_started=login_started,
            login_release=login_release,
        )
        command = [sys.executable, str(PROVIDER)]
        environment = provider_environment(root, gh, runtime_root=runtime_root)
        processes: list[subprocess.Popen[str]] = []

        def read_signal(fd: int) -> None:
            readable, _, _ = select.select([fd], [], [], 5)
            assert readable
            assert os.read(fd, 1) == b"x"

        try:
            first = subprocess.Popen(
                command, env=environment, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            processes.append(first)
            read_signal(mint_fd)
            read_signal(started_fd)

            second = subprocess.Popen(
                command, env=environment, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            processes.append(second)
            read_signal(mint_fd)
            os.write(release_fd, b"go\ngo\n")

            first_stdout, first_stderr = first.communicate(timeout=5)
            second_stdout, second_stderr = second.communicate(timeout=5)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            for fd in (mint_fd, started_fd, release_fd):
                os.close(fd)

        assert first.returncode == 0, first_stderr
        assert second.returncode == 0, second_stderr
        assert json.loads(first_stdout) == json.loads(second_stdout)
        assert call_counts(calls) == (2, 1, 1)
