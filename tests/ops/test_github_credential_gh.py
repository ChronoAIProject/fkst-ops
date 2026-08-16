from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "ops" / "github_credential_gh.py"


def load_wrapper():
    spec = importlib.util.spec_from_file_location("github_credential_gh_under_test", WRAPPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="ascii")
    path.chmod(0o755)
    return path


def wrapper_environment(
    helper: Path, real_gh: Path, *, source: str = "github-app"
) -> dict[str, str]:
    return {
        **os.environ,
        "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
        "FKST_GITHUB_BOT_LOGIN": "declared-bot",
        "FKST_GITHUB_REAL_GH": str(real_gh),
        "FKST_GITHUB_REPO": "owner/repo",
        "FKST_GITHUB_CREDENTIAL_SOURCE": source,
    }


def test_valid_credential_goes_directly_to_requested_command(tmp_path: Path) -> None:
    helper = executable(
        tmp_path / "helper",
        "#!/bin/sh\nprintf '%s\\n' '"
        + json.dumps({
            "token": "secret", "login": "declared-bot", "target": "owner/repo",
            "identity_proof": "target-access-only;bot-login-not-mechanically-proven",
        }, separators=(",", ":"))
        + "'\n",
    )
    calls = tmp_path / "calls"
    real_gh = executable(tmp_path / "gh", f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{calls}"\n')

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "api", "/requested"],
        env=wrapper_environment(helper, real_gh), text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="ascii") == "api /requested\n"


def test_helper_stderr_reaches_output_with_command_and_live_origin(tmp_path: Path) -> None:
    message = "resolver's distinctive raw failure"
    helper = executable(tmp_path / "helper", f"#!/bin/sh\nprintf '%s' \"{message}\" >&2\nexit 9\n")
    real_gh = executable(tmp_path / "gh", "#!/bin/sh\nexit 0\n")

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "api", "/requested"],
        env=wrapper_environment(helper, real_gh), text=True, capture_output=True, check=False,
    )

    assert message in result.stderr
    assert f"command={helper}" in result.stderr
    match = re.search(r"origin=ops/github_credential_gh.py:(\d+)", result.stderr)
    assert match
    source_lines = WRAPPER.read_text(encoding="utf-8").splitlines()
    assert "fail(\"credential-refresh-failed\"" in source_lines[int(match.group(1)) - 1]


def test_spawn_failure_and_timeout_keep_their_exception_messages(tmp_path: Path, capsys) -> None:
    wrapper = load_wrapper()
    helper = executable(tmp_path / "helper", "#!/bin/sh\nexit 0\n")
    environment = {
        "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
        "FKST_GITHUB_BOT_LOGIN": "declared-bot",
    }
    errors = [
        OSError("distinctive spawn failure"),
        subprocess.TimeoutExpired([str(helper)], wrapper.COMMAND_TIMEOUT_SECONDS),
    ]

    for error in errors:
        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(wrapper.subprocess, "run", side_effect=error):
            assert wrapper.credential() is None
        assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize(
    ("source", "proof_source", "accepted"),
    (
        ("github-app", "github-app", True),
        ("github-cli-user", "github-cli-user", True),
        ("github-app", "github-cli-user", False),
        ("github-cli-user", "github-app", False),
    ),
)
def test_credential_identity_proof_is_bound_to_selected_source(
    tmp_path: Path, source: str, proof_source: str, accepted: bool
) -> None:
    proofs = {
        "github-app": "target-access-only;bot-login-not-mechanically-proven",
        "github-cli-user": "login-verified;token-scope-account-wide-not-repository-scoped",
    }
    real_gh = executable(tmp_path / "gh", "#!/bin/sh\nexit 0\n")
    helper = executable(
        tmp_path / f"helper-{source}-{proof_source}",
        "#!/bin/sh\nprintf '%s\\n' '"
        + json.dumps({
            "token": "secret", "login": "declared-bot", "target": "owner/repo",
            "identity_proof": proofs[proof_source],
        }, separators=(",", ":"))
        + "'\n",
    )

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--fkst-auth-check"],
        env=wrapper_environment(helper, real_gh, source=source),
        text=True, capture_output=True, check=False,
    )

    assert (result.returncode == 0) is accepted, (
        source, proof_source, result.stdout, result.stderr
    )
    if not accepted:
        assert "refreshed-credential-proof-missing" in result.stderr


@pytest.mark.parametrize(
    ("source", "include_null_proof"),
    (
        pytest.param(None, False, id="absent-source-omitted-proof"),
        pytest.param("unsupported-source", True, id="unsupported-source-null-proof"),
    ),
)
def test_absent_or_unsupported_source_fails_closed_without_identity_proof(
    tmp_path: Path, source: str | None, include_null_proof: bool
) -> None:
    document = {
        "token": "fixture-token", "login": "declared-bot", "target": "owner/repo",
    }
    if include_null_proof:
        document["identity_proof"] = None
    helper = executable(
        tmp_path / "helper",
        "#!/bin/sh\nprintf '%s\\n' '"
        + json.dumps(document, separators=(",", ":"))
        + "'\n",
    )
    real_gh = executable(tmp_path / "gh", "#!/bin/sh\nexit 0\n")
    environment = wrapper_environment(helper, real_gh)
    if source is None:
        environment.pop("FKST_GITHUB_CREDENTIAL_SOURCE")
    else:
        environment["FKST_GITHUB_CREDENTIAL_SOURCE"] = source

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--fkst-auth-check"],
        env=environment, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 78, result.stderr
    assert "refreshed-credential-source-unsupported" in result.stderr
    assert "fixture-token" not in result.stderr


def test_github_cli_user_command_pins_github_com_credential_environment(
    tmp_path: Path,
) -> None:
    helper = executable(
        tmp_path / "helper",
        "#!/bin/sh\nprintf '%s\\n' '"
        + json.dumps({
            "token": "secret", "login": "declared-bot", "target": "owner/repo",
            "identity_proof": "login-verified;token-scope-account-wide-not-repository-scoped",
        }, separators=(",", ":"))
        + "'\n",
    )
    calls = tmp_path / "calls"
    real_gh = executable(
        tmp_path / "gh",
        "#!/bin/sh\n"
        "[ \"${GH_TOKEN:-}\" = secret ] || exit 40\n"
        "[ \"${GH_HOST:-}\" = github.com ] || exit 41\n"
        "[ -z \"${GH_ENTERPRISE_TOKEN:-}\" ] || exit 42\n"
        "[ -z \"${GITHUB_ENTERPRISE_TOKEN:-}\" ] || exit 43\n"
        f"printf '%s\\n' \"$@\" > '{calls}'\n",
    )
    environment = wrapper_environment(helper, real_gh, source="github-cli-user")
    environment.update({
        "GH_HOST": "enterprise.example.invalid",
        "GH_ENTERPRISE_TOKEN": "hostile-enterprise-token",
        "GITHUB_ENTERPRISE_TOKEN": "hostile-github-enterprise-token",
    })

    result = subprocess.run(
        [sys.executable, str(WRAPPER), "api", "/requested"],
        env=environment, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="ascii") == "api\n/requested\n"
