from __future__ import annotations

import copy
import os
from pathlib import Path
import plistlib
import json
import shutil
import subprocess
import sys
import tomllib
import pytest

from bootstrap.canonical_tree import canonical_tree_sha256
from schema.validator import ValidationError, load_and_resolve
from schema.mechanism_tools import MECHANISM_TOOLS
from watch.generate_artifacts import _discover_tools


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "watch" / "generate_artifacts.py"
FIXTURES = ROOT / "tests" / "schema" / "fixtures"
GIT = shutil.which("git")
assert GIT is not None


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-C", str(root), *args], text=True, capture_output=True, check=True
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
    (repository / "deployment-set.json").write_text(json.dumps({
        "schema": "fkst.ops.declaration-input.v1",
        "declarations": ["deployment.toml"],
    }), encoding="ascii")

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
    git(target, "branch", "integration")
    git(engine, "branch", "integration")
    lock = ""
    for identity, path, pin in (
        ("target-source", target, target_pin), ("engine-source", engine, engine_pin),
        ("fkst-ops", ROOT, ("4" * 40, "sha256-" + "4" * 64)),
    ):
        checkout_role = "mechanism" if identity == "fkst-ops" else "deployment-operated"
        lock += (f'[[external_source]]\nid = "{identity}"\ngit = "{path}"\n'
                 f'checkout_role = "{checkout_role}"\n'
                 f'[external_source.resolved]\nrev = "{pin[0]}"\ntree_sha256 = "{pin[1]}"\n\n')
    (repository / "fkst.lock").write_text(lock)
    return repository, home, tomllib.loads((repository / "deployment.toml").read_text())


def run_generator(
    repository: Path, home: Path, machine_root: Path | None = None,
    bot_login: str | None = "fkst-bot",
) -> subprocess.CompletedProcess[str]:
    mechanism = home / "mechanism"
    if not mechanism.exists():
        shutil.copytree(ROOT, mechanism, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        git(mechanism, "init", "-q")
        git(mechanism, "config", "user.email", "test@example.invalid")
        git(mechanism, "config", "user.name", "test")
        git(mechanism, "add", ".")
        git(mechanism, "commit", "-qm", "fixture mechanism")
        revision = git(mechanism, "rev-parse", "HEAD")
        environment = os.environ.copy()
        environment["PATH"] = str(Path(GIT).parent)
        prior_path = os.environ.get("PATH")
        os.environ["PATH"] = environment["PATH"]
        try:
            tree = canonical_tree_sha256(mechanism, revision)
        finally:
            if prior_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prior_path
        lock_path = repository / "fkst.lock"
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        for entry in lock["external_source"]:
            if entry["id"] == "fkst-ops":
                entry["resolved"] = {"rev": revision, "tree_sha256": tree}
        lines = []
        for entry in lock["external_source"]:
            lines.extend([
                "[[external_source]]", f'id = "{entry["id"]}"',
                f'git = "{entry["git"]}"', f'checkout_role = "{entry["checkout_role"]}"',
                "[external_source.resolved]", f'rev = "{entry["resolved"]["rev"]}"',
                f'tree_sha256 = "{entry["resolved"]["tree_sha256"]}"', "",
            ])
        lock_path.write_text("\n".join(lines), encoding="ascii")
    launchctl = home / "fake-launchctl"
    if not launchctl.exists():
        launchctl.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            "state = pathlib.Path(os.environ['LAUNCHCTL_STATE'])\n"
            "calls = pathlib.Path(os.environ['LAUNCHCTL_CALLS'])\n"
            "with calls.open('a') as stream: stream.write(' '.join(sys.argv[1:]) + '\\n')\n"
            "command = sys.argv[1]\n"
            "if command == 'print':\n"
            "  if state.exists(): print('path = ' + state.read_text().strip()); raise SystemExit(0)\n"
            "  raise SystemExit(113)\n"
            "if command == 'bootstrap': state.write_text(sys.argv[-1] + '\\n')\n"
            "if command == 'bootout': state.unlink(missing_ok=True)\n",
            encoding="ascii",
        )
        launchctl.chmod(0o755)
    environment = {**os.environ, "HOME": str(home)}
    environment["PATH"] = os.pathsep.join((environment.get("PATH", ""), str(Path(GIT).parent)))
    environment.pop("GH_TOKEN", None)
    environment.update({
        "FKST_LAUNCHCTL": str(launchctl),
        "LAUNCHCTL_STATE": str(home / "launchctl.state"),
        "LAUNCHCTL_CALLS": str(home / "launchctl.calls"),
    })
    command = [sys.executable, str(mechanism / "watch" / "generate_artifacts.py"), str(repository)]
    if bot_login is not None:
        command.extend(["--bot-login", bot_login])
    if machine_root is not None:
        command.extend(["--machine-state-root", str(machine_root)])
    return subprocess.run(
        command, env=environment,
        text=True, capture_output=True, check=False,
    )


def test_empty_machine_state_materialises_every_declared_root(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    profile = home / ".fkst" / "machine" / "profile.toml"
    profile_data = tomllib.loads(profile.read_text())
    assert all(Path(path).is_dir() for path in profile_data["roots"].values())
    assert all(Path(path).is_file() and os.access(path, os.X_OK)
               for path in profile_data["binaries"].values())
    checkout = home / ".fkst" / "machine" / "roots" / declaration["deployment"][0]["machine"]["target_checkout"]
    assert git(checkout, "branch", "--show-current") == "integration"
    assert git(checkout, "rev-parse", "HEAD") == git(tmp_path / "target-source", "rev-parse", "HEAD")
    resolved = load_and_resolve(repository / "deployment.toml", profile, repository / "fkst.lock")
    assert resolved["deployment"][0]["machine"]["bot_login"] == "fkst-bot"
    assert resolved["deployment"][0]["managed_bot_logins"] == ["fkst-bot"]
    assert resolved["deployment"][0]["integration"]["integration_branch"] == "integration"
    assert profile_data["defaults"] == {}
    assert "sets" not in profile_data

    plist_path = home / ".fkst" / "machine" / "LaunchAgents" / "com.fkst.cadence.plist"
    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)
    assert plist["StartInterval"] == 300
    arguments = plist["ProgramArguments"]
    assert arguments[arguments.index("--guard-restart-attempt-limit") + 1] == "3"
    profile_argument = Path(arguments[arguments.index("--machine-profile") + 1])
    manifest_argument = Path(arguments[arguments.index("--declaration-manifest") + 1])
    assert profile_argument.parent == manifest_argument.parent
    assert profile_argument.parent.parent.name == "generations"
    assert profile_argument.read_bytes() == profile.read_bytes()
    assert "cadence_schedule=enabled live=yes interval_seconds=300" in result.stdout


def test_machine_integration_reference_generates_resolves_and_hydrates_branch(
    tmp_path: Path,
) -> None:
    repository, home, declaration = prepared(tmp_path)
    logical = "release-track"
    bot_login = "Managed-Bot[bot]"
    concrete_branch = "integration-Managed-Bot"
    declaration_path = repository / "deployment.toml"
    declaration_path.write_text(
        declaration_path.read_text(encoding="ascii")
        .replace(
            'managed_bot_logins = ["fkst-bot"]',
            'managed_bot_logins = ["Managed-Bot"]',
        )
        .replace(
            'integration_branch = "integration"',
            f'integration_branch = "machine:{logical}"',
        ),
        encoding="ascii",
    )
    for source_root in (tmp_path / "target-source", tmp_path / "engine-source"):
        git(source_root, "branch", concrete_branch)

    result = run_generator(repository, home, bot_login=bot_login)

    assert result.returncode == 0, result.stderr
    profile = home / ".fkst" / "machine" / "profile.toml"
    profile_data = tomllib.loads(profile.read_text(encoding="ascii"))
    assert profile_data["defaults"] == {logical: concrete_branch}
    resolved = load_and_resolve(declaration_path, profile, repository / "fkst.lock")
    assert resolved["deployment"][0]["integration"]["integration_branch"] == concrete_branch
    machine = declaration["deployment"][0]["machine"]
    for checkout_name in {machine["target_checkout"], machine["engine_checkout"]}:
        checkout = home / ".fkst" / "machine" / "roots" / checkout_name
        assert git(checkout, "branch", "--show-current") == concrete_branch


def test_provider_uses_every_declared_tool_from_profile_with_restricted_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, home, _ = prepared(tmp_path)
    tool_name = "cargo"
    tool_directory = tmp_path / "discovery-tools"
    tool_directory.mkdir()
    tool = tool_directory / tool_name
    tool.write_text(
        "#!/bin/sh\n"
        "mkdir -p target/debug\n"
        "printf '#!/bin/sh\\nexit 0\\n' > target/debug/engine\n"
        "chmod +x target/debug/engine\n",
        encoding="ascii",
    )
    tool.chmod(0o755)
    declaration_text = (repository / "deployment.toml").read_text(encoding="ascii")
    declaration_text = declaration_text.replace('"./cargo", "build"', f'"{tool_name}", "build"')
    (repository / "deployment.toml").write_text(declaration_text, encoding="ascii")
    monkeypatch.setenv("PATH", str(tool_directory) + os.pathsep + os.environ["PATH"])

    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    profile = home / ".fkst" / "machine" / "profile.toml"
    profile_data = tomllib.loads(profile.read_text(encoding="ascii"))
    assert profile_data["tools"][tool_name] == str(tool.resolve())
    assert set(MECHANISM_TOOLS) <= set(profile_data["tools"])
    resolved = load_and_resolve(repository / "deployment.toml", profile, repository / "fkst.lock")
    deployment = resolved["deployment"][0]
    command_from_profile = deployment["providers"]["engine"]["configuration"]["build_command"]
    assert command_from_profile[0] == str(tool.resolve())
    git(Path(deployment["machine"]["engine_checkout"]), "branch", "--set-upstream-to=origin/integration")

    invocation = {
        "version": "fkst.ops.invocation.v1",
        "contract": "fkst.ops.engine.v1",
        "input": {
            "engine_checkout": deployment["machine"]["engine_checkout"],
            "engine_binary": deployment["machine"]["engine_binary"],
            "expected_branch": deployment["integration"]["integration_branch"],
            "operation": "build",
            "build_command": command_from_profile,
        },
    }
    provider = ROOT / "providers" / "engine.py"
    provider_result = subprocess.run(
        [sys.executable, str(provider)], input=json.dumps(invocation),
        env={**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        text=True, capture_output=True, check=False,
    )
    assert provider_result.returncode == 0, provider_result.stdout + provider_result.stderr

    profile_without_tool = tmp_path / "profile-without-tool.toml"
    profile_without_tool.write_text(
        profile.read_text(encoding="ascii").replace(
            f'"{tool_name}" = "{tool.resolve()}"\n', ""
        ), encoding="ascii",
    )
    with pytest.raises(ValidationError, match=tool_name):
        load_and_resolve(
            repository / "deployment.toml", profile_without_tool, repository / "fkst.lock"
        )


def test_operator_cfg_loads_discovered_cargo_from_generated_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, home, _ = prepared(tmp_path)
    tool_directory = tmp_path / "discovery-tools"
    tool_directory.mkdir()
    cargo = tool_directory / "cargo"
    cargo.write_text(
        "#!/bin/sh\nmkdir -p target/debug\nprintf '#!/bin/sh\\nexit 0\\n' > target/debug/engine\nchmod +x target/debug/engine\n",
        encoding="ascii",
    )
    cargo.chmod(0o755)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace('"./cargo"', '"cargo"'),
        encoding="ascii",
    )
    monkeypatch.setenv("PATH", str(tool_directory) + os.pathsep + os.environ["PATH"])
    generated = run_generator(repository, home)
    assert generated.returncode == 0, generated.stderr

    operator = ROOT / "ops" / "deployment_operator.sh"
    command = f'''set -e
PYTHON="{sys.executable}"
_self_dir="{ROOT / 'ops'}"
RESOLVED_DECLARATION="$(PYTHONPATH="{ROOT}" "$PYTHON" -m schema.validator "$FKST_OPS_DECLARATION" "$FKST_OPS_MACHINE_PROFILE" "$FKST_OPS_LOCK")"
eval "$(sed -n '/^cfg()/,/^}}/p' "{operator}")"
cfg packages
printf '%s' "$CARGO"
'''
    result = subprocess.run(
        ["bash", "-c", command],
        env={
            **os.environ,
            "FKST_OPS_DECLARATION": str(declaration),
            "FKST_OPS_MACHINE_PROFILE": str(home / ".fkst" / "machine" / "profile.toml"),
            "FKST_OPS_LOCK": str(repository / "fkst.lock"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(cargo.absolute())


def test_generation_fails_with_unavailable_declared_tool_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, home, _ = prepared(tmp_path)
    missing_tool = "unavailable-build-tool"
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace('"./cargo"', f'"{missing_tool}"'),
        encoding="ascii",
    )
    tool_directory = tmp_path / "mechanism-tools"
    tool_directory.mkdir()
    for name, tool in MECHANISM_TOOLS.items():
        if not tool.required:
            continue
        executable = tool_directory / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool_directory))

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert f"declared external tool cannot be found: {missing_tool}" in result.stderr
    assert "deployment: packages" in result.stderr
    assert not (home / ".fkst" / "machine" / "profile.toml").exists()


def test_generation_fails_with_unavailable_required_mechanism_tool_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, home, _ = prepared(tmp_path)
    tool_directory = tmp_path / "mechanism-tools"
    tool_directory.mkdir()
    for name in ("codex", "gh-app"):
        executable = tool_directory / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool_directory))

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert "mechanism tool cannot be found: gh" in result.stderr
    assert not (home / ".fkst" / "machine" / "profile.toml").exists()


def test_generation_requires_codex_as_a_path_delivered_mechanism_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, home, _ = prepared(tmp_path)
    tool_directory = tmp_path / "mechanism-tools"
    tool_directory.mkdir()
    for name in ("gh", "gh-app"):
        executable = tool_directory / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool_directory))

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert "mechanism tool cannot be found: codex" in result.stderr
    assert not (home / ".fkst" / "machine" / "profile.toml").exists()


def test_undeclared_cargo_does_not_affect_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_directory = tmp_path / "mechanism-tools"
    tool_directory.mkdir()
    for name in ("codex", "gh", "gh-app"):
        executable = tool_directory / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
    monkeypatch.setattr(shutil, "which", lambda name: (
        str(tool_directory / name) if name in {"codex", "gh", "gh-app"} else None
    ))

    repository, _, _ = prepared(tmp_path)
    declaration = tomllib.loads((repository / "deployment.toml").read_text(encoding="ascii"))
    declaration["provider"][0]["configuration"]["build_command"][0] = "./cargo"
    tools = _discover_tools([(repository / "deployment.toml", declaration)])

    assert tools == {
        name: str(tool_directory / name)
        for name, tool in MECHANISM_TOOLS.items()
        if tool.required
    }


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


def test_guard_restart_attempt_limit_must_match_across_declarations(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    first = repository / "deployment.toml"
    second = repository / "second.toml"
    second.write_text(
        first.read_text(encoding="ascii").replace(
            "guard_restart_attempt_limit = 3",
            "guard_restart_attempt_limit = 4",
        ),
        encoding="ascii",
    )
    (repository / "deployment-set.json").write_text(
        json.dumps({
            "schema": "fkst.ops.declaration-input.v1",
            "declarations": ["deployment.toml", "second.toml"],
        }),
        encoding="ascii",
    )

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert (
        "all deployment declarations must use one guard_restart_attempt_limit"
        in result.stderr
    )


def test_dirty_checkout_is_refused_without_destroying_work(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    assert run_generator(repository, home).returncode == 0
    checkout = home / ".fkst" / "machine" / "roots" / declaration["deployment"][0]["machine"]["target_checkout"]
    tracked = checkout / "providers" / "engine-board"
    tracked.write_text("tampered\n")
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "refusing to replace existing work" in result.stderr
    assert tracked.read_text() == "tampered\n"


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


def test_regeneration_preserves_advanced_deployment_branch(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    assert run_generator(repository, home).returncode == 0
    checkout = home / ".fkst" / "machine" / "roots" / declaration["deployment"][0]["machine"]["target_checkout"]
    (checkout / "advanced").write_text("branch state\n", encoding="ascii")
    git(checkout, "add", "advanced")
    git(checkout, "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "advance")
    advanced = git(checkout, "rev-parse", "HEAD")

    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    assert git(checkout, "branch", "--show-current") == "integration"
    assert git(checkout, "rev-parse", "HEAD") == advanced


@pytest.mark.parametrize("state", ["wrong-branch", "behind", "diverged"])
def test_regeneration_refuses_non_reproducible_checkout_states(
    tmp_path: Path, state: str
) -> None:
    repository, home, declaration = prepared(tmp_path)
    assert run_generator(repository, home).returncode == 0
    checkout = home / ".fkst" / "machine" / "roots" / declaration["deployment"][0]["machine"]["target_checkout"]
    evidence = checkout / "local-evidence"
    if state == "wrong-branch":
        git(checkout, "checkout", "-qb", "personal")
        evidence.write_text("wrong branch\n", encoding="ascii")
        git(checkout, "add", "local-evidence")
        git(checkout, "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "personal")
    elif state == "behind":
        pinned = git(tmp_path / "target-source", "rev-parse", "HEAD")
        parent = git(tmp_path / "target-source", "rev-parse", f"{pinned}^") if git(tmp_path / "target-source", "rev-list", "--count", "HEAD") != "1" else None
        if parent is None:
            (tmp_path / "target-source" / "later").write_text("later", encoding="ascii")
            git(tmp_path / "target-source", "add", "later")
            git(tmp_path / "target-source", "commit", "-qm", "later")
            parent = pinned
        git(checkout, "checkout", "-q", "--detach", parent)
        evidence.write_text("behind evidence\n", encoding="ascii")
    else:
        git(checkout, "checkout", "-qb", "diverged", "HEAD^") if git(checkout, "rev-list", "--count", "HEAD") != "1" else git(checkout, "checkout", "-qb", "diverged")
        evidence.write_text("diverged evidence\n", encoding="ascii")
        git(checkout, "add", "local-evidence")
        git(checkout, "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "diverged")
    head = git(checkout, "rev-parse", "HEAD")
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "refusing to replace existing work" in result.stderr
    assert git(checkout, "rev-parse", "HEAD") == head
    assert evidence.read_text().endswith("evidence\n") or state == "wrong-branch"


def test_scratch_machine_root_leaves_live_machine_state_untouched(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    live = home / ".fkst" / "machine"
    live.mkdir(parents=True)
    sentinels = [
        live / "profile.toml", live / "declarations.json",
        live / "LaunchAgents" / "com.fkst.cadence.plist",
        live / "roots" / "existing" / "work",
    ]
    for path in sentinels:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("live\n", encoding="ascii")
    scratch = tmp_path / "scratch-machine"
    result = run_generator(repository, home, scratch)
    assert result.returncode == 0, result.stderr
    assert all(path.read_text() == "live\n" for path in sentinels)
    assert not (home / "launchctl.calls").exists()
    assert (scratch / "profile.toml").is_file()
    assert (scratch / "declarations.json").is_file()
    assert (scratch / "LaunchAgents" / "com.fkst.cadence.plist").is_file()


def test_generation_rejects_repository_root_symlink_to_test_fixture(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    hidden = repository / "tests" / "fixtures" / "hidden.toml"
    hidden.parent.mkdir(parents=True)
    hidden.write_text((repository / "deployment.toml").read_text(), encoding="ascii")
    (repository / "adopted.toml").symlink_to(hidden.relative_to(repository))
    (repository / "deployment-set.json").write_text(json.dumps({
        "schema": "fkst.ops.declaration-input.v1", "declarations": ["adopted.toml"],
    }), encoding="ascii")
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "forbidden canonical declaration target" in result.stderr
    assert not (home / ".fkst" / "machine" / "profile.toml").exists()


def test_generation_rejects_missing_remote_integration_branch(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    git(tmp_path / "target-source", "branch", "-D", "integration")
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "remote integration branch integration is missing" in result.stderr


def test_generation_rejects_pin_outside_remote_integration_history(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    source_root = tmp_path / "target-source"
    original_branch = git(source_root, "branch", "--show-current")
    git(source_root, "checkout", "--orphan", "unrelated")
    git(source_root, "rm", "-q", "-r", "-f", ".")
    (source_root / "unrelated").write_text("orphan history\n", encoding="ascii")
    git(source_root, "add", "unrelated")
    git(source_root, "commit", "-qm", "unrelated")
    git(source_root, "branch", "-f", "integration")
    git(source_root, "checkout", "-q", original_branch)

    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "does not match pin" in result.stderr


def test_hydration_failure_preserves_coherent_live_control_state(tmp_path: Path) -> None:
    repository, home, declaration_data = prepared(tmp_path)
    first = run_generator(repository, home)
    assert first.returncode == 0, first.stderr
    machine = home / ".fkst" / "machine"
    profile = machine / "profile.toml"
    manifest = machine / "declarations.json"
    launch_agent = machine / "LaunchAgents" / "com.fkst.cadence.plist"
    before = {path: path.read_bytes() for path in (profile, manifest, launch_agent)}

    checkout = machine / "roots" / declaration_data["deployment"][0]["machine"]["target_checkout"]
    (checkout / "providers" / "engine-board").write_text("unusable\n", encoding="ascii")
    failed = run_generator(repository, home)
    assert failed.returncode == 2
    assert {path: path.read_bytes() for path in before} == before

    operator = tmp_path / "operator"
    calls = tmp_path / "scheduled.calls"
    operator.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\nexit 0\n", encoding="ascii"
    )
    operator.chmod(0o755)
    environment = {**os.environ, "CALLS": str(calls)}
    scheduled = subprocess.run(
        [
            sys.executable, str(ROOT / "watch" / "cadence_round.py"),
            "--deployment-repository", str(repository),
            "--machine-profile", str(profile),
            "--declaration-manifest", str(manifest),
            "--ledger", str(tmp_path / "ledger.jsonl"),
            "--guard-restart-attempt-limit", "0",
            "--operator-entry", str(operator),
        ],
        env=environment, text=True, capture_output=True, check=False,
    )
    assert scheduled.returncode == 0, scheduled.stderr
    assert len(calls.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "checkpoint", ["public-link-1", "public-link-2", "public-link-3", "generation-selected"]
)
def test_abrupt_death_never_exposes_mixed_control_generation(
    tmp_path: Path, checkpoint: str
) -> None:
    import signal
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    destinations = (
        machine / "profile.toml",
        machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
    staging = machine / "staging"
    staging.mkdir()
    candidates = []
    for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
        candidate = staging / name
        candidate.write_bytes(f"new-{index}".encode("ascii"))
        candidates.append(candidate)

    child = os.fork()
    if child == 0:
        generator._publication_checkpoint = lambda point: (
            os.kill(os.getpid(), signal.SIGKILL) if point == checkpoint else None
        )
        generator._publish_control_files(
            dict(zip(destinations, candidates)), destinations[-1], True, False,
            machine / "control",
        )
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    observed = tuple(path.read_bytes() if path.exists() else None for path in destinations)
    old = tuple(None for _ in destinations)
    new = tuple(f"new-{index}".encode("ascii") for index in range(3))
    assert observed in (old, new)


def test_generation_retention_protects_selected_and_running_generations(tmp_path: Path) -> None:
    import watch.generate_artifacts as generator

    control = tmp_path / "control"
    generations = control / "generations"
    generations.mkdir(parents=True)
    selected = generations / "selected"
    launch_source = generations / "launch-source"
    profile_reference = generations / "profile-reference"
    manifest_reference = generations / "manifest-reference"
    abandoned = generations / "abandoned"
    for generation in (
        selected, launch_source, profile_reference, manifest_reference, abandoned
    ):
        generation.mkdir()
    (control / "current").symlink_to("generations/selected")
    running_agent = launch_source / "com.fkst.cadence.plist"
    running_agent.write_bytes(plistlib.dumps({"ProgramArguments": [
        str(profile_reference / "profile.toml"),
        str(manifest_reference / "declarations.json"),
    ]}))

    generator._prune_generations(control, generator.ScheduleState(True, running_agent))

    assert {path.name for path in generations.iterdir()} == {
        "selected", "launch-source", "profile-reference", "manifest-reference",
    }


def test_concurrent_publications_are_serialised_per_control_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import fcntl
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    control = machine / "control"
    destinations = (
        machine / "profile.toml", machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    attempted_second = threading.Event()
    release_second_attempt = threading.Event()
    resumed_second_attempt = threading.Event()
    entered_first = threading.Event()
    release_first = threading.Event()
    entered_second = threading.Event()
    errors: list[BaseException] = []

    def checkpoint(point: str) -> None:
        if point == "lock-attempt" and threading.current_thread().name == "publisher-b":
            attempted_second.set()
            assert release_second_attempt.wait(5)
            resumed_second_attempt.set()
            return
        if point != "generation-selected":
            return
        if threading.current_thread().name == "publisher-a":
            entered_first.set()
            assert release_first.wait(5)
        else:
            entered_second.set()

    monkeypatch.setattr(generator, "_publication_checkpoint", checkpoint)

    def publish(label: str) -> None:
        staging = machine / f"staging-{label}"
        staging.mkdir(parents=True)
        candidates = []
        for index, name in enumerate((
            "profile.toml", "declarations.json", "com.fkst.cadence.plist"
        )):
            candidate = staging / name
            candidate.write_bytes(f"{label}-{index}".encode("ascii"))
            candidates.append(candidate)
        try:
            generator._publish_control_files(
                dict(zip(destinations, candidates)), destinations[-1], True, False,
                control, f"generation-{label}",
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=publish, args=("a",), name="publisher-a")
    second = threading.Thread(target=publish, args=("b",), name="publisher-b")
    first.start()
    assert entered_first.wait(5)
    second.start()
    assert attempted_second.wait(5), "second publisher did not reach the lock attempt"
    release_second_attempt.set()
    assert resumed_second_attempt.wait(5), "second publisher did not resume its lock attempt"
    with (control / ".publish.lock").open("a+b") as probe:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not entered_second.is_set(), "second publisher entered the publication transaction"
    release_first.set()
    first.join(5)
    second.join(5)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert entered_second.is_set()
    assert (control / "current").resolve().name == "generation-b"
    assert {path.name for path in (control / "generations").iterdir()} == {"generation-b"}


def test_repeated_isolated_publication_is_bounded(tmp_path: Path) -> None:
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    destinations = (
        machine / "profile.toml", machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    for run in range(4):
        staging = machine / f"staging-{run}"
        staging.mkdir(parents=True)
        candidates = []
        for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
            candidate = staging / name
            candidate.write_bytes(f"{run}-{index}".encode("ascii"))
            candidates.append(candidate)
        generator._publish_control_files(
            dict(zip(destinations, candidates)), destinations[-1], True, False,
            machine / "control",
        )
        assert len(list((machine / "control" / "generations").iterdir())) == 1


def _reconciliation_launchctl(tmp_path: Path, restoration_fails: bool) -> tuple[Path, Path]:
    executable = tmp_path / "launchctl"
    state = tmp_path / "schedule.state"
    calls = tmp_path / "schedule.calls"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "state=pathlib.Path(os.environ['LAUNCHCTL_STATE'])\n"
        "calls=pathlib.Path(os.environ['LAUNCHCTL_CALLS'])\n"
        "with calls.open('a') as stream: stream.write(' '.join(sys.argv[1:])+'\\n')\n"
        "command=sys.argv[1]\n"
        "if command == 'print':\n"
        "  if state.exists(): print('path = '+state.read_text().strip()); raise SystemExit(0)\n"
        "  raise SystemExit(113)\n"
        "if command == 'bootout': state.unlink(missing_ok=True); raise SystemExit(0)\n"
        "if command == 'enable': raise SystemExit(0)\n"
        "if command == 'bootstrap':\n"
        "  source=sys.argv[-1]\n"
        "  if source != os.environ['LEGACY_PATH'] or os.environ.get('RESTORE_FAIL') == '1':\n"
        "    print('injected bootstrap failure', file=sys.stderr); raise SystemExit(1)\n"
        "  state.write_text(source+'\\n'); raise SystemExit(0)\n",
        encoding="ascii",
    )
    executable.chmod(0o755)
    return executable, state


@pytest.mark.parametrize("restoration_fails", [False, True])
def test_reconciliation_failure_restores_exact_legacy_schedule_or_surfaces_both_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restoration_fails: bool
) -> None:
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    legacy = tmp_path / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy agent", encoding="ascii")
    executable, state = _reconciliation_launchctl(tmp_path, restoration_fails)
    state.write_text(str(legacy), encoding="ascii")
    monkeypatch.setenv("FKST_LAUNCHCTL", str(executable))
    monkeypatch.setenv("LAUNCHCTL_STATE", str(state))
    monkeypatch.setenv("LAUNCHCTL_CALLS", str(tmp_path / "schedule.calls"))
    monkeypatch.setenv("LEGACY_PATH", str(legacy))
    monkeypatch.setenv("RESTORE_FAIL", "1" if restoration_fails else "0")

    destinations = (
        machine / "profile.toml", machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    staging = machine / "staging"
    staging.mkdir(parents=True)
    candidates = []
    for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
        candidate = staging / name
        candidate.write_bytes(f"new-{index}".encode("ascii"))
        candidates.append(candidate)

    with pytest.raises(ValueError) as raised:
        generator._publish_control_files(
            dict(zip(destinations, candidates)), destinations[-1], True, True,
            machine / "control",
        )
    if restoration_fails:
        assert "publication failed: cannot activate cadence schedule" in str(raised.value)
        assert "schedule restoration failed: cannot activate cadence schedule" in str(raised.value)
        assert not state.exists()
    else:
        assert state.read_text().strip() == str(legacy)
        assert f"bootstrap gui/{os.getuid()} {legacy}" in (tmp_path / "schedule.calls").read_text()
    assert not any(path.exists() for path in destinations)
    # The legacy source is deliberately not a plist, so reference evidence cannot
    # be obtained and pruning fails closed rather than guessing about a live job.
    generations = machine / "control" / "generations"
    assert len(list(generations.iterdir())) == (0 if restoration_fails else 1)

    if not restoration_fails:
        monkeypatch.setenv("RESTORE_FAIL", "0")
        state.unlink()
        retry_staging = machine / "retry-staging"
        retry_staging.mkdir()
        retry_candidates = []
        for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
            candidate = retry_staging / name
            candidate.write_bytes(f"retry-{index}".encode("ascii"))
            retry_candidates.append(candidate)
        generator._publish_control_files(
            dict(zip(destinations, retry_candidates)), destinations[-1], True, False,
            machine / "control",
        )
        assert len(list(generations.iterdir())) == 1


def test_profile_uses_explicit_bot_login_and_does_not_write_managed_bot_set(
    tmp_path: Path,
) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    declaration["deployment"][0]["managed_bot_logins"] = ["bot-a", "bot-b"]
    from watch.generate_artifacts import _profile_text

    profile = tomllib.loads(_profile_text(
        [(tmp_path / "declaration.toml", declaration)], tmp_path, {}, bot_login="bot-b"
    ))

    assert profile["credentials"] == {"github-bot": "bot-b"}
    assert "sets" not in profile


def test_profile_populates_every_referenced_machine_default(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    deployment = declaration["deployment"][0]
    deployment["integration"]["integration_branch"] = "machine:first-branch"
    second = copy.deepcopy(deployment)
    second["integration"]["integration_branch"] = "machine:second-branch"
    declaration["deployment"].append(second)
    from watch.generate_artifacts import _profile_text

    profile = tomllib.loads(_profile_text(
        [(tmp_path / "declaration.toml", declaration)],
        tmp_path,
        {},
        bot_login="fkst-bot[bot]",
    ))

    assert profile["defaults"] == {
        "first-branch": "integration-fkst-bot",
        "second-branch": "integration-fkst-bot",
    }


def test_generator_requires_explicit_bot_login_cli_argument(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    result = run_generator(repository, home, bot_login=None)
    assert result.returncode == 2
    assert "--bot-login" in result.stderr
    assert "required" in result.stderr


def test_generator_rejects_bot_login_outside_declared_roster(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    result = run_generator(repository, home, bot_login="outside-bot")
    assert result.returncode == 2
    assert "outside-bot is not in" in result.stderr
    assert "managed_bot_logins" in result.stderr


def test_generator_rejects_bot_login_with_empty_normalized_identity(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    result = run_generator(repository, home, bot_login="[bot]")
    assert result.returncode == 2
    assert "--bot-login: must not normalize to an empty identity" in result.stderr


def test_generator_normalizes_bot_suffix_without_changing_login_case(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace(
            'managed_bot_logins = ["fkst-bot"]',
            'managed_bot_logins = ["Managed-Bot"]',
        ),
        encoding="ascii",
    )

    accepted = run_generator(repository, home, bot_login="Managed-Bot[bot]")
    assert accepted.returncode == 0, accepted.stderr
    profile = tomllib.loads(
        (home / ".fkst" / "machine" / "profile.toml").read_text(encoding="ascii")
    )
    assert profile["credentials"]["github-bot"] == "Managed-Bot[bot]"

    rejected = run_generator(repository, home, bot_login="managed-bot[bot]")
    assert rejected.returncode == 2
    assert "is not in" in rejected.stderr


def test_profileless_missing_machine_bot_login_is_a_typed_generation_error(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    text = declaration.read_text(encoding="ascii")
    profile_block = '''[deployment.github_devloop_profile]
version = "1"
id = "github-devloop-default"
data = { rollup_label = "rollup" }
producer_binding = "github-board"

'''
    declaration.write_text(
        text.replace(profile_block, "").replace('bot_login = "github-bot"\n', ""),
        encoding="ascii",
    )

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert "artifact generation failed" in result.stderr
    assert "deployment[0].machine.bot_login is required for artifact generation" in result.stderr
    assert "Traceback" not in result.stderr


def test_profile_rejects_empty_or_malformed_declared_bot_roster(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    home = tmp_path / "home"
    home.mkdir()
    for value in ([], ["valid", ""]):
        declaration["deployment"][0]["managed_bot_logins"] = value
        try:
            from watch.generate_artifacts import _profile_text
            _profile_text(
                [(tmp_path / "declaration.toml", declaration)], home, {}, bot_login="valid"
            )
        except ValueError as exc:
            assert "non-empty string list" in str(exc)
        else:
            raise AssertionError("invalid bot roster declaration was accepted")


def test_mechanism_checkout_is_materialised_detached(tmp_path: Path) -> None:
    from watch.generate_artifacts import _materialise_checkout

    home = tmp_path / "home"
    home.mkdir()
    mechanism = tmp_path / "mechanism-source"
    revision, tree = source(mechanism, {"bin/entry": "#!/bin/sh\nexit 0\n"})
    checkout = tmp_path / "mechanism-checkout"
    _materialise_checkout(
        checkout, str(mechanism), revision, tree, home, "mechanism"
    )
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert git(checkout, "branch", "--show-current") == ""
