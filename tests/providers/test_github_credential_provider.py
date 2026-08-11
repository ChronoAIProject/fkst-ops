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


def invoke(resolver: Path, gh: Path, expected: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ, "FKST_GITHUB_CREDENTIAL_RESOLVER": str(resolver),
        "FKST_GITHUB_REAL_GH": str(gh), "FKST_GITHUB_BOT_LOGIN": expected,
        "FKST_GITHUB_REPO": TARGET, "FKST_GITHUB_CREDENTIAL_SOURCE": "github-app",
        **extra,
    }
    return subprocess.run([sys.executable, str(PROVIDER)], env=env, text=True,
                          capture_output=True, check=False)


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
