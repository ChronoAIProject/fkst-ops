#!/usr/bin/env python3
"""Mint a repository-scoped GitHub App installation credential."""

from __future__ import annotations

import json
import os
import subprocess
import sys


TOKEN_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")


def fail(cause: str) -> int:
    print(f"credential-source-failed: {cause}", file=sys.stderr)
    return 1


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in TOKEN_NAMES:
        environment.pop(name, None)
    return environment


def run(arguments: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            arguments, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    expected = os.environ.get("FKST_GITHUB_BOT_LOGIN", "")
    target = os.environ.get("FKST_GITHUB_REPO", "")
    resolver = os.environ.get("FKST_GITHUB_CREDENTIAL_RESOLVER", "")
    real_gh = os.environ.get("FKST_GITHUB_REAL_GH", "")
    source = os.environ.get("FKST_GITHUB_CREDENTIAL_SOURCE", "")
    if not expected:
        return fail("declared-bot-login-missing")
    if not target or target.count("/") != 1:
        return fail("declared-target-invalid")
    if source != "github-app":
        return fail("credential-source-not-github-app")
    for executable, cause in ((resolver, "github-app-resolver-unavailable"),
                              (real_gh, "github-cli-unavailable")):
        if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            return fail(cause)

    # Never let an inherited credential select the resolver's pass-through branch.
    minted = run([resolver, "token", "--target", target], env=clean_environment())
    if minted is None or minted.returncode != 0 or not minted.stdout.strip():
        return fail("github-app-token-mint-failed")
    token = minted.stdout.strip()
    if "\n" in token or "\r" in token:
        return fail("token-result-invalid")

    verification_environment = clean_environment()
    verification_environment["GH_TOKEN"] = token
    repositories = run(
        [real_gh, "api", "--paginate", "/installation/repositories",
         "--jq", ".repositories[].full_name"],
        env=verification_environment,
    )
    accessible = set(repositories.stdout.splitlines()) if repositories and repositories.returncode == 0 else set()
    if target not in accessible:
        return fail("declared-target-not-accessible-to-installation")

    # Installation tokens do not resolve through /user, so the bot login cannot
    # be mechanically derived from this token. The resolver source and target
    # access are verified; login remains a declaration checked by the caller.
    json.dump({"token": token, "login": expected, "target": target,
               "identity_proof": "target-access-only;bot-login-not-mechanically-proven"},
              sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
