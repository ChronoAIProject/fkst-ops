from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tomllib

from bootstrap.canonical_tree import canonical_tree_sha256
from schema.validator import load_and_resolve


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "watch" / "generate_artifacts.py"
FIXTURES = ROOT / "tests" / "schema" / "fixtures"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def source(root: Path, files: dict[str, str]) -> tuple[str, str]:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="ascii")
        if content.startswith("#!"):
            path.chmod(0o755)
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    revision = git(root, "rev-parse", "HEAD")
    return revision, canonical_tree_sha256(root, revision)


def prepared(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    repository = tmp_path / "deployment"
    home = tmp_path / "home"
    repository.mkdir()
    home.mkdir()
    shutil.copy(FIXTURES / "packages.toml", repository / "deployment.toml")
    text = (repository / "deployment.toml").read_text()
    text = text.replace('configuration = { build_command = ["make", "engine"] }',
                        'configuration = { build_command = ["./cargo", "build", "-p", "engine"] }')
    (repository / "deployment.toml").write_text(text)

    target = tmp_path / "target-source"
    engine = tmp_path / "engine-source"
    target_pin = source(target, {
        "packages/github-devloop/entry": "x",
        "packages/github-devloop-pr/entry": "x",
        "packages/github-devloop-integration/entry": "x",
        "providers/engine-board": "#!/bin/sh\nexit 0\n",
        "providers/github-board": "#!/bin/sh\nexit 0\n",
    })
    engine_pin = source(engine, {
        "bin/build-provider": "#!/bin/sh\nexit 0\n",
        "cargo": "#!/bin/sh\nmkdir -p target/debug\nprintf '#!/bin/sh\\nexit 0\\n' > target/debug/engine\nchmod +x target/debug/engine\n",
    })
    lock = ""
    for identity, path, pin in (
        ("target-source", target, target_pin), ("engine-source", engine, engine_pin),
        ("fkst-ops", ROOT, ("4" * 40, "sha256-" + "4" * 64)),
    ):
        lock += (f'[[external_source]]\nid = "{identity}"\ngit = "{path}"\n'
                 f'[external_source.resolved]\nrev = "{pin[0]}"\ntree_sha256 = "{pin[1]}"\n\n')
    (repository / "fkst.lock").write_text(lock)
    return repository, home, tomllib.loads((repository / "deployment.toml").read_text())


def run_generator(repository: Path, home: Path) -> subprocess.CompletedProcess[str]:
    launchctl = home / "fake-launchctl"
    if not launchctl.exists():
        launchctl.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            "state = pathlib.Path(os.environ['LAUNCHCTL_STATE'])\n"
            "calls = pathlib.Path(os.environ['LAUNCHCTL_CALLS'])\n"
            "with calls.open('a') as stream: stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "command = sys.argv[1]\n"
            "if command == 'print': raise SystemExit(0 if state.exists() else 113)\n"
            "if command == 'bootstrap': state.write_text('live\\n')\n"
            "if command == 'bootout': state.unlink(missing_ok=True)\n",
            encoding="ascii",
        )
        launchctl.chmod(0o755)
    environment = {**os.environ, "HOME": str(home)}
    environment.pop("GH_TOKEN", None)
    environment.update({
        "FKST_LAUNCHCTL": str(launchctl),
        "LAUNCHCTL_STATE": str(home / "launchctl.state"),
        "LAUNCHCTL_CALLS": str(home / "launchctl.calls"),
    })
    return subprocess.run(
        [sys.executable, str(GENERATOR), str(repository)], env=environment,
        text=True, capture_output=True, check=False,
    )


def test_empty_machine_state_materialises_every_declared_root(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    profile = repository / ".fkst" / "machine-profile.toml"
    profile_data = tomllib.loads(profile.read_text())
    assert all(Path(path).is_dir() for path in profile_data["roots"].values())
    assert all(Path(path).is_file() and os.access(path, os.X_OK)
               for path in profile_data["binaries"].values())
    resolved = load_and_resolve(repository / "deployment.toml", profile, repository / "fkst.lock")
    assert resolved["deployment"][0]["machine"]["bot_login"] == "fkst-bot"
    assert resolved["deployment"][0]["machine"]["managed_bot_set"] == ["fkst-bot"]

    plist_path = home / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    with plist_path.open("rb") as stream:
        assert plistlib.load(stream)["StartInterval"] == 300
    assert "cadence_schedule=enabled live=yes interval_seconds=300" in result.stdout


def test_generation_activates_and_deactivates_declared_schedule(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"

    enabled = run_generator(repository, home)
    assert enabled.returncode == 0, enabled.stderr
    assert (home / "launchctl.state").is_file()

    declaration.write_text(
        declaration.read_text().replace("cadence_enabled = true", "cadence_enabled = false"),
        encoding="ascii",
    )
    disabled = run_generator(repository, home)
    assert disabled.returncode == 0, disabled.stderr
    assert not (home / "launchctl.state").exists()
    assert "cadence_schedule=disabled live=no interval_seconds=300" in disabled.stdout
    calls = (home / "launchctl.calls").read_text().splitlines()
    assert any(line.startswith("bootstrap ") for line in calls)
    assert any(line.startswith("bootout ") for line in calls)
    assert any(line.startswith("disable ") for line in calls)


def test_schedule_parameters_are_required_declaration_values(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text().replace("cadence_enabled = true\n", ""), encoding="ascii"
    )
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "cadence_enabled must be a boolean" in result.stderr


def test_wrong_checkout_content_is_replaced(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    assert run_generator(repository, home).returncode == 0
    checkout = home / ".fkst" / "machine" / "roots" / declaration["deployment"][0]["machine"]["target_checkout"]
    tracked = checkout / "providers" / "engine-board"
    tracked.write_text("tampered\n")
    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    assert tracked.read_text().startswith("#!/bin/sh")


def test_regeneration_preserves_accumulated_state_and_skips_settled_build(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    assert run_generator(repository, home).returncode == 0
    base = home / ".fkst" / "machine"
    durable = base / "roots" / declaration["deployment"][0]["machine"]["durable"] / "history"
    durable.write_text("keep")
    binary = base / "bin" / declaration["deployment"][0]["machine"]["engine_binary"]
    first_mtime = binary.lstat().st_mtime_ns
    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    assert durable.read_text() == "keep"
    assert binary.lstat().st_mtime_ns == first_mtime


def test_rejects_missing_or_ambiguous_declared_bot_login(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    home = tmp_path / "home"
    home.mkdir()
    for value in ([], ["one", "two"]):
        declaration["deployment"][0]["managed_bot_logins"] = value
        try:
            from watch.generate_artifacts import _profile_text
            _profile_text([(tmp_path / "declaration.toml", declaration)], home)
        except ValueError as exc:
            assert "exactly one" in str(exc)
        else:
            raise AssertionError("invalid bot login declaration was accepted")
