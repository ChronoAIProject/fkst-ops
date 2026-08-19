"""A call that fails after the credential was issued must not be called an auth failure.

The proxy runs `gh` with its stderr passed straight through, so it never sees the response
status. It previously resolved that absence of evidence by naming the one cause it could not
observe, marking every timeout, 5xx and cancelled request `github-authentication-failed
HEALTH=UNHEALTHY`. Health is derived by counting that class, so a transient turned the
deployment unhealthy and pointed diagnosis at credentials that had just been verified.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROXY = ROOT / "ops" / "github_credential_gh.py"


def load_proxy():
    specification = importlib.util.spec_from_file_location("ops_github_credential_gh", PROXY)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class GhCallFailureClassTest(unittest.TestCase):
    def run_proxy(self, gh_exit: int, gh_stderr: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "helper"
            proof = "login-verified;token-scope-account-wide-not-repository-scoped"
            document = (
                '{"token":"t","login":"declared-user","target":"o/r",'
                f'"identity_proof":"{proof}"}}'
            )
            helper.write_text(f"#!/bin/sh\ncat <<'JSON'\n{document}\nJSON\n", encoding="ascii")
            helper.chmod(0o755)
            gh = root / "gh"
            gh.write_text(
                f"#!/bin/sh\nprintf '%s' '{gh_stderr}' >&2\nexit {gh_exit}\n", encoding="ascii"
            )
            gh.chmod(0o755)
            environment = os.environ.copy()
            environment.update({
                "FKST_GITHUB_CREDENTIAL_HELPER": str(helper),
                "FKST_GITHUB_BOT_LOGIN": "declared-user",
                "FKST_GITHUB_REAL_GH": str(gh),
                "FKST_GITHUB_CREDENTIAL_SOURCE": "github-cli-user",
                "FKST_GITHUB_REPO": "o/r",
            })
            return subprocess.run(
                [sys.executable, str(PROXY), "pr", "view", "1"],
                env=environment, text=True, capture_output=True, check=False,
            )

    def test_a_transport_failure_is_not_an_authentication_failure(self) -> None:
        result = self.run_proxy(1, "HTTP 499: 499 (https://api.github.com/graphql)")
        self.assertNotIn("error_class=github-authentication-failed", result.stderr)
        self.assertIn("error_class=github-call-failed", result.stderr)

    def test_a_failed_call_asserts_no_health_verdict(self) -> None:
        # Health is counted from the authentication class; a call failure has no evidence
        # for any verdict, so it must not claim one.
        result = self.run_proxy(1, "HTTP 502: 502")
        self.assertNotIn("HEALTH=", result.stderr)

    def test_the_proxy_still_forwards_the_exit_status(self) -> None:
        result = self.run_proxy(3, "boom")
        self.assertEqual(result.returncode, 3)

    def test_gh_stderr_is_still_passed_through(self) -> None:
        result = self.run_proxy(1, "HTTP 499: 499")
        self.assertIn("HTTP 499", result.stderr)

    def test_a_successful_call_reports_nothing(self) -> None:
        result = self.run_proxy(0, "")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("error_class=", result.stderr)

    def test_the_authentication_class_is_still_reachable_for_a_refused_credential(self) -> None:
        module = load_proxy()
        refused = module.health_fact(
            "credential-source-failed: github-cli-user-login-verification-failed "
            "raw_error=GitHub answered 401"
        )
        self.assertIn("error_class=github-authentication-failed", refused)
        self.assertIn("HEALTH=UNHEALTHY", refused)


if __name__ == "__main__":
    unittest.main()
