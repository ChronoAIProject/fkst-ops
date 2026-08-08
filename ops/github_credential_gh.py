#!/usr/bin/env python3
"""Run gh with a freshly issued, identity-checked credential."""

from __future__ import annotations

import json
import os
import subprocess
import sys


HEALTH_FACT = "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY"


def fail(message: str) -> int:
    print(f"{HEALTH_FACT} MSG={message}", file=sys.stderr)
    return 78


def credential() -> tuple[str, str] | None:
    helper = os.environ.get("FKST_GITHUB_CREDENTIAL_HELPER", "")
    expected = os.environ.get("FKST_GITHUB_BOT_LOGIN", "")
    if not helper or not os.path.isfile(helper) or not os.access(helper, os.X_OK):
        fail("credential-helper-unavailable")
        return None
    try:
        issued = subprocess.run(
            [helper], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=30,
        )
        document = json.loads(issued.stdout) if issued.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        document = None
    if not isinstance(document, dict):
        fail("credential-refresh-failed")
        return None
    token, login = document.get("token"), document.get("login")
    if not isinstance(token, str) or not token or not isinstance(login, str) or not login:
        fail("credential-refresh-returned-invalid-result")
        return None
    if not expected or login != expected:
        fail("refreshed-credential-identity-mismatch")
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
        return fail("real-gh-unavailable")
    environment = os.environ.copy()
    environment["GH_TOKEN"] = issued[0]
    environment.pop("GITHUB_TOKEN", None)
    try:
        completed = subprocess.run([real_gh, *sys.argv[1:]], env=environment, check=False)
    except OSError:
        return fail("real-gh-could-not-start")
    if completed.returncode != 0:
        # Authentication failures from gh are not reliably classified by exit status.
        # Mark every failed GitHub call unhealthy; the next call refreshes independently.
        print(f"{HEALTH_FACT} MSG=github-call-failed exit={completed.returncode}", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
