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

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'github.com' "
        "'  + Logged in to github.com account fixture-bot (GH_TOKEN)' "
        "'  - Active account: true'\n",
        encoding="ascii",
    )
    gh.chmod(0o755)
    environment = {**os.environ, "HOME": str(home), "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [sys.executable, str(GENERATOR), str(repository)],
        env=environment, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    profile = repository / ".fkst" / "machine-profile.toml"
    resolved = load_and_resolve(repository / "deployment.toml", profile, repository / "fkst.lock")
    assert resolved["deployment"][0]["machine"]["bot_login"] == "fixture-bot"
    assert resolved["deployment"][0]["machine"]["managed_bot_set"] == [
        "fkst-bot", "fkst-review-bot"
    ]

    plist_path = home / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)
    assert plist["StartInterval"] == 300
    assert plist["ProgramArguments"][5] == str(profile)
