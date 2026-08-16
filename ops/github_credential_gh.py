#!/usr/bin/env python3
"""Run gh with a freshly issued, identity-checked credential."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


HEALTH_FACT = "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY"
COMMAND_TIMEOUT_SECONDS = 30


def fail(message: str, error: str, *, command: list[str] | None = None) -> int:
    frame = inspect.currentframe()
    assert frame is not None and frame.f_back is not None
    caller = frame.f_back
    source = Path(caller.f_code.co_filename).resolve().relative_to(Path(__file__).resolve().parents[1])
    command_text = f" command={shlex.join(command)}" if command else ""
    print(f"{HEALTH_FACT} MSG={message} origin={source}:{caller.f_lineno}{command_text} raw_error={error}",
          file=sys.stderr)
    return 78


def credential() -> tuple[str, str] | None:
    helper = os.environ.get("FKST_GITHUB_CREDENTIAL_HELPER", "")
    expected = os.environ.get("FKST_GITHUB_BOT_LOGIN", "")
    if not helper or not os.path.isfile(helper) or not os.access(helper, os.X_OK):
        fail("credential-helper-unavailable", f"credential helper is not executable: {helper!r}")
        return None
    helper_command = [helper]
    if (os.environ.get("FKST_GITHUB_CREDENTIAL_SOURCE") == "github-cli-user"
            and sys.argv[1:] == ["--fkst-auth-check"]):
        helper_command.append("--fkst-auth-check")
    try:
        issued = subprocess.run(
            helper_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        fail("credential-refresh-failed", str(error), command=helper_command)
        return None
    if issued.returncode != 0:
        fail("credential-refresh-failed", issued.stderr, command=helper_command)
        return None
    try:
        document = json.loads(issued.stdout)
    except json.JSONDecodeError as error:
        fail("credential-refresh-failed", str(error), command=helper_command)
        return None
    if not isinstance(document, dict):
        fail("credential-refresh-failed", f"credential helper returned {type(document).__name__}, not an object",
             command=helper_command)
        return None
    token, login = document.get("token"), document.get("login")
    if not isinstance(token, str) or not token or not isinstance(login, str) or not login:
        fail("credential-refresh-returned-invalid-result",
             "credential helper result has no non-empty string token and login", command=helper_command)
        return None
    if not expected or login != expected:
        fail("refreshed-credential-identity-mismatch",
             f"credential login {login!r} does not match declared login {expected!r}", command=helper_command)
        return None
    expected_target = os.environ.get("FKST_GITHUB_REPO", "")
    if document.get("target") != expected_target:
        fail("refreshed-credential-target-mismatch",
             f"credential target {document.get('target')!r} does not match declared target {expected_target!r}",
             command=helper_command)
        return None
    source = os.environ.get("FKST_GITHUB_CREDENTIAL_SOURCE", "")
    expected_proofs = {
        "github-app": "target-access-only;bot-login-not-mechanically-proven",
        "github-cli-user": "login-verified;token-scope-account-wide-not-repository-scoped",
    }
    if source not in expected_proofs:
        fail("refreshed-credential-source-unsupported",
             f"FKST_GITHUB_CREDENTIAL_SOURCE is {source!r}", command=helper_command)
        return None
    if document.get("identity_proof") != expected_proofs[source]:
        fail("refreshed-credential-proof-missing",
             f"credential identity proof is {document.get('identity_proof')!r} for source {source!r}",
             command=helper_command)
        return None
    real_gh = os.environ.get("FKST_GITHUB_REAL_GH", "")
    if not real_gh or not os.path.isfile(real_gh) or not os.access(real_gh, os.X_OK):
        fail("real-gh-unavailable", f"GitHub CLI is not executable: {real_gh!r}")
        return None
    return token, login


def main() -> int:
    issued = credential()
    if issued is None:
        return 78
    if sys.argv[1:] == ["--fkst-auth-check"]:
        return 0
    real_gh = os.environ.get("FKST_GITHUB_REAL_GH", "")
    if not real_gh or not os.path.isfile(real_gh) or not os.access(real_gh, os.X_OK):
        return fail("real-gh-unavailable", f"GitHub CLI is not executable: {real_gh!r}")
    environment = os.environ.copy()
    environment["GH_TOKEN"] = issued[0]
    environment.pop("GITHUB_TOKEN", None)
    if os.environ.get("FKST_GITHUB_CREDENTIAL_SOURCE") == "github-cli-user":
        environment["GH_HOST"] = "github.com"
        environment.pop("GH_ENTERPRISE_TOKEN", None)
        environment.pop("GITHUB_ENTERPRISE_TOKEN", None)
    command = [real_gh, *sys.argv[1:]]
    try:
        completed = subprocess.run(command, env=environment, check=False)
    except OSError as error:
        return fail("real-gh-could-not-start", str(error), command=command)
    if completed.returncode != 0:
        # Authentication failures from gh are not reliably classified by exit status.
        # Mark every failed GitHub call unhealthy; the next call refreshes independently.
        print(f"{HEALTH_FACT} MSG=github-call-failed exit={completed.returncode}", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
