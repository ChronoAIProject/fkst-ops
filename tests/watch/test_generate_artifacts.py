from __future__ import annotations

import copy
import os
from pathlib import Path
import plistlib
import json
import shutil
import subprocess
import sys
import time
import tomllib
import pytest

from bootstrap.canonical_tree import canonical_tree_sha256
from schema.validator import ValidationError, load_and_resolve
from schema.mechanism_tools import MECHANISM_TOOLS
from watch.generate_artifacts import _discover_tools
from generate_artifacts_test_support import (
    FIXTURES,
    GIT,
    ROOT,
    git,
    prepared,
    run_generator,
    source,
)

def test_empty_machine_state_materialises_every_declared_root(tmp_path: Path) -> None:
    repository, home, declaration = prepared(tmp_path)
    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    profile = home / ".fkst" / "machine" / "profile.toml"
    profile_data = tomllib.loads(profile.read_text())
    assert all(Path(path).is_dir() for path in profile_data["roots"].values())
    machine = declaration["deployment"][0]["machine"]
    revision = git(
        Path(profile_data["roots"][machine["platform_checkout"]]),
        "show",
        "HEAD:.control/engine-ref",
    )
    binary = Path(f'{profile_data["binaries"][machine["engine_binary"]]}-{revision}')
    assert binary.is_file() and os.access(binary, os.X_OK)
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
    target_checkout = home / ".fkst" / "machine" / "roots" / machine["target_checkout"]
    engine_checkout = home / ".fkst" / "machine" / "roots" / machine["engine_checkout"]
    assert git(target_checkout, "branch", "--show-current") == concrete_branch
    assert git(engine_checkout, "branch", "--show-current") == ""


def test_machine_credential_source_uses_explicit_github_cli_user_selection(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace(
            'configuration = { source = "github-app" }',
            'configuration = { source = "machine:credential-source" }',
        ),
        encoding="ascii",
    )

    result = run_generator(
        repository, home, github_credential_source="github-cli-user"
    )

    assert result.returncode == 0, result.stderr
    profile = home / ".fkst" / "machine" / "profile.toml"
    profile_data = tomllib.loads(profile.read_text(encoding="ascii"))
    assert profile_data["defaults"] == {"credential-source": "github-cli-user"}
    resolved = load_and_resolve(declaration, profile, repository / "fkst.lock")
    provider = resolved["deployment"][0]["providers"]["github_credential"]
    assert provider["configuration"] == {"source": "github-cli-user"}


def test_machine_credential_source_requires_explicit_selection(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace(
            'configuration = { source = "github-app" }',
            'configuration = { source = "machine:credential-source" }',
        ),
        encoding="ascii",
    )

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert "--github-credential-source is required" in result.stderr


def test_machine_credential_source_rejects_invalid_explicit_selection(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace(
            'configuration = { source = "github-app" }',
            'configuration = { source = "machine:credential-source" }',
        ),
        encoding="ascii",
    )

    result = run_generator(
        repository, home, github_credential_source="ambient-account"
    )

    assert result.returncode == 2
    assert "--github-credential-source must be github-app or github-cli-user" in result.stderr


def test_literal_github_app_source_does_not_require_explicit_selection(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)

    result = run_generator(repository, home)

    assert result.returncode == 0, result.stderr
    profile = home / ".fkst" / "machine" / "profile.toml"
    profile_data = tomllib.loads(profile.read_text(encoding="ascii"))
    assert profile_data["defaults"] == {}


def test_literal_github_app_source_rejects_invalid_explicit_selection(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)

    result = run_generator(
        repository, home, github_credential_source="ambient-account"
    )

    assert result.returncode == 2
    assert "--github-credential-source must be github-app or github-cli-user" in result.stderr


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
    expected_revision = git(Path(deployment["machine"]["engine_checkout"]), "rev-parse", "HEAD")
    invocation = {
        "version": "fkst.ops.invocation.v1",
        "contract": "fkst.ops.engine.v1",
        "input": {
            "engine_checkout": deployment["machine"]["engine_checkout"],
            "engine_binary": f'{deployment["machine"]["engine_binary"]}-{expected_revision}',
            "expected_revision": expected_revision,
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
    machine = declaration["deployment"][0]["machine"]
    revision = git(base / "roots" / machine["platform_checkout"], "show", "HEAD:.control/engine-ref")
    binary = base / "bin" / f'{machine["engine_binary"]}-{revision}'
    first_mtime = binary.lstat().st_mtime_ns
    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    assert durable.read_text() == "keep"
    assert binary.lstat().st_mtime_ns == first_mtime


def test_engine_branch_advance_without_platform_revision_change_skips_build(
    tmp_path: Path,
) -> None:
    repository, home, declaration = prepared(tmp_path)
    engine_source = tmp_path / "engine-source"
    platform_source = tmp_path / "target-source"
    (engine_source / "fkst.workspace.toml").write_text(
        '[[external_sources]]\n'
        'id = "platform"\n'
        f'git = "{platform_source}"\n'
        'packages = ["github-devloop", "github-devloop-pr", '
        '"github-devloop-integration"]\n',
        encoding="ascii",
    )
    git(engine_source, "add", "fkst.workspace.toml")
    git(engine_source, "commit", "-qm", "add target workspace")
    git(engine_source, "branch", "-f", "integration", "HEAD")
    selected_engine_revision = git(engine_source, "rev-parse", "HEAD")
    (platform_source / ".control" / "engine-ref").write_text(
        selected_engine_revision + "\n", encoding="ascii"
    )
    git(platform_source, "add", ".control/engine-ref")
    git(platform_source, "commit", "-qm", "select workspace engine revision")
    git(platform_source, "branch", "-f", "integration", "HEAD")
    declaration_path = repository / "deployment.toml"
    declaration_text = declaration_path.read_text(encoding="ascii")
    declaration_path.write_text(
        declaration_text
        .replace('lock_ref = "target-source"', 'lock_ref = "engine-source"', 1)
        .replace('target_checkout = "packages-host"', 'target_checkout = "engine-target"')
        .replace(
            'implementation = "engine-source:bin/build-provider"',
            'implementation = "fkst-ops:providers/engine.py"',
        ),
        encoding="ascii",
    )
    declaration = tomllib.loads(declaration_path.read_text(encoding="ascii"))
    assert run_generator(repository, home).returncode == 0
    base = home / ".fkst" / "machine"
    machine = declaration["deployment"][0]["machine"]
    derived_revision = git(
        base / "roots" / machine["platform_checkout"], "show", "HEAD:.control/engine-ref"
    )
    binary = base / "bin" / f'{machine["engine_binary"]}-{derived_revision}'
    target_checkout = base / "roots" / machine["target_checkout"]
    platform_checkout = base / "roots" / machine["platform_checkout"]
    engine_checkout = base / "roots" / machine["engine_checkout"]
    selected_revision = git(engine_checkout, "rev-parse", "HEAD")
    platform_revision = git(platform_checkout, "rev-parse", "HEAD")
    first_mtime = binary.lstat().st_mtime_ns
    log_directory = base / "roots" / machine["logs"]
    supervise_log = log_directory / "packages-sv-1.log"
    supervise_log.write_text(
        f"EVENT=code_provenance github-devloop@{platform_revision[:8]} "
        f"ENGINE_VER={selected_revision[:8]}\nMSG=event runtime running\n",
        encoding="ascii",
    )
    supervise = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            "supervise",
            "--project-root",
            str(target_checkout),
            "fixture",
        ]
    )
    time.sleep(0.1)

    (engine_source / "branch-only-change").write_text("not selected\n", encoding="ascii")
    git(engine_source, "add", "branch-only-change")
    git(engine_source, "commit", "-qm", "advance engine branch only")
    git(engine_source, "branch", "-f", "integration", "HEAD")
    branch_revision = git(engine_source, "rev-parse", "integration")

    try:
        result = run_generator(repository, home)
        assert result.returncode == 0, result.stderr
        operator = subprocess.run(
            [
                str(home / "mechanism" / "bin" / "fkst-ops"),
                "--deployment-dir",
                str(repository),
                "--declaration",
                str(declaration_path),
                "--machine-profile",
                str(base / "profile.toml"),
                "--lock",
                str(repository / "fkst.lock"),
                "sync",
                "packages",
            ],
            env={**os.environ, "FKST_OPS_CACHE_ROOT": str(home / "operator-cache")},
            text=True,
            capture_output=True,
            check=False,
        )
        assert operator.returncode == 0, operator.stdout + operator.stderr
        assert supervise.poll() is None
        assert "current (no restart needed)" in operator.stdout
    finally:
        supervise.terminate()
        supervise.wait(timeout=5)

    assert git(target_checkout, "rev-parse", "HEAD") == branch_revision
    assert git(engine_checkout, "rev-parse", "HEAD") == selected_revision
    assert binary.lstat().st_mtime_ns == first_mtime


def test_generation_reports_malformed_engine_derivation_without_traceback(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace('path = ".control/engine-ref"\n', ""),
        encoding="ascii",
    )

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert "engine_revision.path" in result.stderr
    assert "Traceback" not in result.stderr


def test_shared_binary_stem_publishes_each_platform_declared_revision(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    engine_source = tmp_path / "engine-source"
    (engine_source / "second-engine-state").write_text("second\n", encoding="ascii")
    git(engine_source, "add", "second-engine-state")
    git(engine_source, "commit", "-qm", "second engine revision")
    second_revision = git(engine_source, "rev-parse", "HEAD")

    second_platform = tmp_path / "second-platform-source"
    source(second_platform, {
        ".control/engine-ref": second_revision + "\n",
        "packages/github-devloop/entry": "x",
        "packages/github-devloop-pr/entry": "x",
        "packages/github-devloop-integration/entry": "x",
        "providers/engine-board": "#!/bin/sh\nexit 0\n",
        "providers/github-board": "#!/bin/sh\nexit 0\n",
    })
    git(second_platform, "branch", "integration")
    with (repository / "fkst.lock").open("a", encoding="ascii") as stream:
        stream.write(
            "[[external_source]]\n"
            'id = "second-platform"\n'
            f'git = "{second_platform}"\n'
            'checkout_role = "deployment-operated"\n\n'
        )

    first = (repository / "deployment.toml").read_text(encoding="ascii")
    first_target = tomllib.loads(first)["deployment"][0]["target_identity"]
    second = (
        first.replace('id = "packages"', 'id = "second"', 1)
        .replace(f'target_identity = "{first_target}"', 'target_identity = "example/second"')
        .replace('lock_ref = "target-source"', 'lock_ref = "second-platform"')
        .replace('implementation = "target-source:', 'implementation = "second-platform:')
        .replace('target_checkout = "packages-host"', 'target_checkout = "second-platform"')
        .replace('platform_checkout = "packages-host"', 'platform_checkout = "second-platform"')
        .replace('engine_checkout = "engine-source"', 'engine_checkout = "second-engine-checkout"')
        .replace('durable = "packages-durable"', 'durable = "second-durable"')
        .replace('runtime = "packages-runtime"', 'runtime = "second-runtime"')
        .replace('logs = "packages-logs"', 'logs = "second-logs"')
    )
    (repository / "second.toml").write_text(second, encoding="ascii")
    (repository / "deployment-set.json").write_text(
        json.dumps({
            "schema": "fkst.ops.declaration-input.v1",
            "declarations": ["deployment.toml", "second.toml"],
        }),
        encoding="ascii",
    )

    result = run_generator(repository, home)

    assert result.returncode == 0, result.stderr
    binary_root = home / ".fkst" / "machine" / "bin"
    assert (binary_root / f"engine-{second_revision}").is_file()
    assert not (binary_root / f"engine-{second_revision}").is_symlink()
    first_revision = git(tmp_path / "target-source", "show", "HEAD:.control/engine-ref")
    assert (binary_root / f"engine-{first_revision}").is_file()
    assert not (binary_root / f"engine-{first_revision}").is_symlink()


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
