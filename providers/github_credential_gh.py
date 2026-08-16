#!/usr/bin/env python3
"""Mint and verify a configured GitHub credential."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


TOKEN_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")
COMMAND_TIMEOUT_SECONDS = 30


def fail(cause: str, error: str, *, command: list[str] | None = None) -> int:
    frame = inspect.currentframe()
    assert frame is not None and frame.f_back is not None
    caller = frame.f_back
    source = Path(caller.f_code.co_filename).resolve().relative_to(Path(__file__).resolve().parents[1])
    command_text = f" command={shlex.join(command)}" if command else ""
    print(f"credential-source-failed: {cause} origin={source}:{caller.f_lineno}{command_text} raw_error={error}",
          file=sys.stderr)
    return 1


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in TOKEN_NAMES:
        environment.pop(name, None)
    return environment


def run(arguments: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str] | Exception:
    try:
        return subprocess.run(
            arguments, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return error
def main() -> int:
    expected = os.environ.get("FKST_GITHUB_BOT_LOGIN", "")
    target = os.environ.get("FKST_GITHUB_REPO", "")
    resolver = os.environ.get("FKST_GITHUB_CREDENTIAL_RESOLVER", "")
    real_gh = os.environ.get("FKST_GITHUB_REAL_GH", "")
    source = os.environ.get("FKST_GITHUB_CREDENTIAL_SOURCE", "")
    if not expected:
        return fail("declared-bot-login-missing", "FKST_GITHUB_BOT_LOGIN is empty")
    if not target or target.count("/") != 1:
        return fail("declared-target-invalid", f"FKST_GITHUB_REPO is not owner/repository: {target!r}")
    if source not in {"github-app", "github-cli-user"}:
        return fail("credential-source-unsupported", f"FKST_GITHUB_CREDENTIAL_SOURCE is {source!r}")

    if source == "github-app":
        for executable, cause in ((resolver, "github-app-resolver-unavailable"),
                                  (real_gh, "github-cli-unavailable")):
            if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
                return fail(cause, f"executable is unavailable: {executable!r}")

        # Never let an inherited credential select the resolver's pass-through branch.
        mint_command = [resolver, "token", "--target", target]
        minted = run(mint_command, env=clean_environment())
        if isinstance(minted, Exception):
            return fail("github-app-token-mint-failed", str(minted), command=mint_command)
        if minted.returncode != 0:
            return fail("github-app-token-mint-failed", minted.stderr, command=mint_command)
        if not minted.stdout.strip():
            return fail("github-app-token-mint-failed", "credential resolver returned empty stdout",
                        command=mint_command)
        token = minted.stdout.strip()
        if "\n" in token or "\r" in token:
            return fail("token-result-invalid", "credential resolver returned more than one line",
                        command=mint_command)

        verification_environment = clean_environment()
        verification_environment["GH_TOKEN"] = token
        repository_command = [real_gh, "api", "--paginate", "/installation/repositories",
                              "--jq", ".repositories[].full_name"]
        repositories = run(repository_command, env=verification_environment)
        if isinstance(repositories, Exception):
            return fail("declared-target-not-accessible-to-installation", str(repositories),
                        command=repository_command)
        if repositories.returncode != 0:
            return fail("declared-target-not-accessible-to-installation", repositories.stderr,
                        command=repository_command)
        accessible = set(repositories.stdout.splitlines())
        if target not in accessible:
            return fail("declared-target-not-accessible-to-installation",
                        f"declared target {target!r} is absent from the installation repositories",
                        command=repository_command)

        # Installation tokens do not resolve through /user, so the bot login cannot
        # be mechanically derived from this token. The resolver source and target
        # access are verified; login remains a declaration checked by the caller.
        json.dump({"token": token, "login": expected, "target": target,
                   "identity_proof": "target-access-only;bot-login-not-mechanically-proven"},
                  sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0

    if not real_gh or not os.path.isfile(real_gh) or not os.access(real_gh, os.X_OK):
        return fail("github-cli-unavailable", f"executable is unavailable: {real_gh!r}")

    mint_command = [real_gh, "auth", "token", "--hostname", "github.com", "--user", expected]
    minted = run(mint_command, env=clean_environment())
    if isinstance(minted, Exception):
        return fail("github-cli-user-token-mint-failed", str(minted), command=mint_command)
    if minted.returncode != 0:
        return fail(
            "github-cli-user-token-mint-failed",
            f"GitHub CLI exited with status {minted.returncode}",
            command=mint_command,
        )
    if not minted.stdout.strip():
        return fail("github-cli-user-token-mint-failed", "GitHub CLI returned empty stdout",
                    command=mint_command)
    token = minted.stdout.strip()
    if "\n" in token or "\r" in token:
        return fail("token-result-invalid", "GitHub CLI returned more than one line",
                    command=mint_command)

    verification_environment = clean_environment()
    verification_environment["GH_HOST"] = "github.com"
    verification_environment.pop("GH_ENTERPRISE_TOKEN", None)
    verification_environment.pop("GITHUB_ENTERPRISE_TOKEN", None)
    verification_environment["GH_TOKEN"] = token
    login_command = [real_gh, "api", "/user", "--jq", ".login"]
    login = run(login_command, env=verification_environment)
    if isinstance(login, Exception):
        return fail("github-cli-user-login-verification-failed", str(login), command=login_command)
    if login.returncode != 0:
        return fail(
            "github-cli-user-login-verification-failed",
            f"GitHub CLI exited with status {login.returncode}",
            command=login_command,
        )
    if login.stdout.splitlines() != [expected]:
        return fail(
            "github-cli-user-login-mismatch",
            "verified GitHub login did not exactly match the declared login",
            command=login_command,
        )

    permission_command = [real_gh, "api", f"repos/{target}", "--jq", ".permissions.push"]
    permission = run(permission_command, env=verification_environment)
    if isinstance(permission, Exception):
        return fail("github-cli-user-push-verification-failed", str(permission),
                    command=permission_command)
    if permission.returncode != 0:
        return fail(
            "github-cli-user-push-verification-failed",
            f"GitHub CLI exited with status {permission.returncode}",
            command=permission_command,
        )
    if permission.stdout.splitlines() != ["true"]:
        return fail(
            "github-cli-user-push-permission-missing",
            "GitHub-reported target push permission was not exactly true",
            command=permission_command,
        )

    json.dump({"token": token, "login": expected, "target": target,
               "identity_proof": "login-verified;token-scope-account-wide-not-repository-scoped"},
              sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
