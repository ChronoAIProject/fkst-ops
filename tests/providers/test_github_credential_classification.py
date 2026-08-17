"""A failed verification must report what GitHub established, not the exit status.

`gh api` exits 1 for every non-2xx response, so a 503 during a GitHub outage was
indistinguishable from a refused token. Both were reported as an authentication failure,
and because the operator derives health by counting that class, an outage marked the
deployment unhealthy and pointed diagnosis at the credentials.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile

from test_github_credential_provider import (
    TARGET,
    invoke,
    make_github_cli_user_gh,
)


ROOT = Path(__file__).resolve().parents[2]


def load_ops_module():
    specification = importlib.util.spec_from_file_location(
        "ops_github_credential_gh", ROOT / "ops" / "github_credential_gh.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def classification_for(api_status: str, fail_call: str) -> str:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        gh, _ = make_github_cli_user_gh(root, fail_call=fail_call, api_status=api_status)
        result = invoke(
            root / "missing-app-resolver", gh, "declared-user", source="github-cli-user"
        )
    assert result.returncode != 0, result.stdout
    return result.stderr


def test_login_refused_by_github_is_an_authentication_failure() -> None:
    stderr = classification_for("401 Unauthorized", "user")
    assert "login-verification-failed" in stderr
    assert "unavailable" not in stderr
    assert "GitHub answered 401" in stderr


def test_login_forbidden_by_github_is_an_authentication_failure() -> None:
    stderr = classification_for("403 Forbidden", "user")
    assert "login-verification-failed" in stderr
    assert "unavailable" not in stderr
    assert "GitHub answered 403" in stderr


def test_login_outage_is_unavailable_not_an_authentication_failure() -> None:
    stderr = classification_for("503 Service Unavailable", "user")
    assert "login-verification-unavailable" in stderr
    assert "login-verification-failed" not in stderr
    assert "GitHub answered 503" in stderr


def test_login_with_no_observable_status_is_unavailable() -> None:
    stderr = classification_for("", "user")
    assert "login-verification-unavailable" in stderr
    assert "no HTTP status observed" in stderr


def test_push_outage_is_unavailable_not_an_authentication_failure() -> None:
    stderr = classification_for("503 Service Unavailable", "repository")
    assert "push-verification-unavailable" in stderr
    assert "push-verification-failed" not in stderr


def test_push_refused_by_github_is_an_authentication_failure() -> None:
    stderr = classification_for("403 Forbidden", "repository")
    assert "push-verification-failed" in stderr
    assert "unavailable" not in stderr
    assert "GitHub answered 403" in stderr


def test_operator_reports_an_unreachable_source_as_unavailable() -> None:
    ops = load_ops_module()
    fact = ops.health_fact(
        "credential-source-failed: github-cli-user-login-verification-unavailable "
        "raw_error=GitHub CLI exited with status 1; GitHub answered 503"
    )
    assert "error_class=github-credential-source-unavailable" in fact
    assert "github-authentication-failed" not in fact
    # Health is derived by counting the authentication class, so an outage must not
    # assert a health verdict at all.
    assert "HEALTH=" not in fact


def test_operator_still_reports_a_refused_credential_as_unhealthy() -> None:
    ops = load_ops_module()
    fact = ops.health_fact(
        "credential-source-failed: github-cli-user-login-verification-failed "
        "raw_error=GitHub CLI exited with status 1; GitHub answered 401"
    )
    assert "error_class=github-authentication-failed" in fact
    assert "HEALTH=UNHEALTHY" in fact


def test_target_fixture_is_the_declared_repository() -> None:
    # Guards the shared fixture contract this module depends on.
    assert "/" in TARGET
