from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tomllib

from schema.validator import load_and_resolve


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "watch" / "generate_artifacts.py"
FIXTURES = ROOT / "tests" / "schema" / "fixtures"


def test_regenerates_valid_profile_and_declared_plist_interval(tmp_path: Path) -> None:
    repository = tmp_path / "deployment"
    home = tmp_path / "home"
    repository.mkdir()
    home.mkdir()
    shutil.copy(FIXTURES / "packages.toml", repository / "deployment.toml")
    shutil.copy(FIXTURES / "fkst.lock", repository / "fkst.lock")

    declaration = tomllib.loads((repository / "deployment.toml").read_text())
    deployment = declaration["deployment"][0]
    machine = deployment["machine"]
    base = home / ".fkst" / "machine"
    for field in (
        "target_checkout", "platform_checkout", "engine_checkout", "durable",
        "runtime", "logs", "rate_pool",
    ):
        (base / "roots" / machine[field]).mkdir(parents=True, exist_ok=True)
    (base / "bin").mkdir(parents=True)

    platform = base / "roots" / machine["platform_checkout"]
    target = base / "roots" / machine["target_checkout"]
    engine = base / "roots" / machine["engine_checkout"]
    for package in deployment["packages"]["platform"]:
        (platform / "packages" / package).mkdir(parents=True, exist_ok=True)
    for provider in declaration["provider"]:
        lock_ref, relative = provider["implementation"].split(":", 1)
        if lock_ref == "fkst-ops":
            continue
        source = {"target-source": target, "engine-source": engine}[lock_ref]
        executable = source / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)

    environment = {**os.environ, "HOME": str(home)}
    environment.pop("GH_TOKEN", None)

    result = subprocess.run(
        [sys.executable, str(GENERATOR), str(repository)],
        env=environment, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    profile = repository / ".fkst" / "machine-profile.toml"
    plist_path = home / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    without_token = (profile.read_bytes(), plist_path.read_bytes())

    with_token = {**environment, "GH_TOKEN": "must-not-affect-generated-artifacts"}
    repeated = subprocess.run(
        [sys.executable, str(GENERATOR), str(repository)],
        env=with_token, text=True, capture_output=True, check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert without_token == (profile.read_bytes(), plist_path.read_bytes())

    resolved = load_and_resolve(repository / "deployment.toml", profile, repository / "fkst.lock")
    assert resolved["deployment"][0]["machine"]["bot_login"] == "fkst-bot"
    assert resolved["deployment"][0]["machine"]["managed_bot_set"] == ["fkst-bot"]

    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)
    assert plist["StartInterval"] == 300
    assert plist["ProgramArguments"][5] == str(profile)


def test_rejects_missing_or_ambiguous_declared_bot_login(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    home = tmp_path / "home"
    home.mkdir()
    for value, expected in (([], "exactly one"), (["one", "two"], "exactly one")):
        declaration["deployment"][0]["managed_bot_logins"] = value
        try:
            from watch.generate_artifacts import _profile_text

            _profile_text([(tmp_path / "declaration.toml", declaration)], home)
        except ValueError as exc:
            assert "declaration" in str(exc)
            assert expected in str(exc)
        else:
            raise AssertionError("invalid bot login declaration was accepted")
