#!/usr/bin/env python3
"""Composition checks for the deployment child's generated PATH."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from schema.mechanism_tools import MECHANISM_TOOLS, MechanismTool
from watch.generate_artifacts import _discover_tools, _profile_text

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
SYSTEM_WHICH = shutil.which


def _operator_loader() -> str:
    source = OPERATOR.read_text(encoding="utf-8")
    start = source.index("MECHANISM_TOOL_ASSIGNMENTS=")
    end_marker = 'eval "$MECHANISM_TOOL_ASSIGNMENTS"'
    end = source.index(end_marker, start) + len(end_marker)
    return source[start:end]


def _tool_declaration(*names: str) -> list[tuple[Path, dict[str, object]]]:
    providers = [
        {
            "id": f"provider-{index}",
            "configuration": {f"build_{index}_command": [name]},
        }
        for index, name in enumerate(names)
    ]
    return [
        (
            Path("declaration.toml"),
            {
                "provider": providers,
                "deployment": [
                    {
                        "id": "fixture",
                        "providers": {
                            f"binding-{index}": provider["id"]
                            for index, provider in enumerate(providers)
                        },
                    }
                ],
            },
        )
    ]


def _discover_fixture_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *declared: str
) -> dict[str, str]:
    locations: dict[str, str] = {}
    for index, name in enumerate(sorted({*MECHANISM_TOOLS, *declared})):
        directory = tmp_path / f"discovered-{index // 2}"
        directory.mkdir(exist_ok=True)
        executable = directory / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
        locations[name] = str(executable)
    monkeypatch.setattr(shutil, "which", locations.get)
    return _discover_tools(_tool_declaration(*declared))


def _load_child_path(profile: Path, launcher_path: str) -> list[str]:
    command = f'''PYTHON="{sys.executable}"
_repo_root="{ROOT}"
_self_dir="{ROOT / 'ops'}"
DEPLOYMENT_PYTHON="{sys.executable}"
FKST_OPS_MACHINE_PROFILE="$1"
{_operator_loader()}
printf '%s' "$DEPLOYMENT_CHILD_PATH"
'''
    result = subprocess.run(
        ["/bin/bash", "-c", command, "test", str(profile)],
        env={"PATH": launcher_path}, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.split(os.pathsep)


def _generated_child_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *declared: str
) -> tuple[dict[str, str], list[str], Path]:
    discovered = _discover_fixture_tools(tmp_path, monkeypatch, *declared)
    profile = tmp_path / "profile.toml"
    profile.write_text(
        _profile_text([], tmp_path, discovered, bot_login="fkst-test-bot"),
        encoding="ascii",
    )
    launcher = tmp_path / "launcher-only"
    launcher.mkdir()
    return discovered, _load_child_path(profile, str(launcher)), launcher


def test_every_enumerated_mechanism_tool_is_loaded_from_profile() -> None:
    source = OPERATOR.read_text(encoding="utf-8")
    loader = source[
        source.index("MECHANISM_TOOL_ASSIGNMENTS="):
        source.index("DEPLOYMENT_OPERATOR_DEPLOYMENTS=")
    ]
    assert "from schema.mechanism_tools import MECHANISM_TOOLS" in loader
    assert "for name, tool in MECHANISM_TOOLS.items()" in loader
    for name, tool in MECHANISM_TOOLS.items():
        assert f'"{name}"' in (ROOT / "schema" / "mechanism_tools.py").read_text()
        if tool.shell_variable is not None:
            assert f'"{tool.shell_variable}"' in (
                ROOT / "schema" / "mechanism_tools.py"
            ).read_text()


def test_child_path_contains_every_discovered_tool_and_not_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovered, child_path, launcher = _generated_child_path(tmp_path, monkeypatch, "cargo")
    standard_path = os.confstr("CS_PATH") or os.defpath
    expected = [
        str(ROOT / "ops"),
        str(Path(sys.executable).parent),
        *(str(Path(location).parent) for location in discovered.values()),
        *os.get_exec_path({"PATH": standard_path}),
    ]
    assert child_path == list(dict.fromkeys(expected))
    assert str(launcher) not in child_path
    assert {str(Path(location).parent) for location in discovered.values()} <= set(child_path)
    composed = os.pathsep.join(child_path)
    assert SYSTEM_WHICH("codex", path=composed) == discovered["codex"]
    resolved_python3 = SYSTEM_WHICH("python3", path=composed)
    assert resolved_python3 is not None
    assert os.path.samefile(resolved_python3, sys.executable)
    assert SYSTEM_WHICH("gh", path=composed) == str(ROOT / "ops" / "gh")


def test_newly_declared_tool_joins_child_path_without_loader_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_tool = "zz-future-build-tool"
    assert new_tool not in OPERATOR.read_text(encoding="utf-8")
    assert new_tool not in MECHANISM_TOOLS

    discovered, child_path, _ = _generated_child_path(tmp_path, monkeypatch, new_tool)

    assert new_tool in discovered
    assert str(Path(discovered[new_tool]).parent) in child_path
    assert SYSTEM_WHICH(new_tool, path=os.pathsep.join(child_path)) == discovered[new_tool]


def test_child_path_is_not_an_opt_in_mechanism_tool_property() -> None:
    assert "child_path" not in MechanismTool._fields


def test_provider_child_reaches_sibling_of_declared_build_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_tool_name = "zz-fkst-build-tool"
    sibling_name = "zz-fkst-build-tool-sibling"
    discovered, child_path, launcher = _generated_child_path(
        tmp_path, monkeypatch, build_tool_name
    )
    build_tool = Path(discovered[build_tool_name])
    sibling = build_tool.parent / sibling_name
    marker = tmp_path / "resolved-sibling"
    binary = tmp_path / "engine"
    provider = tmp_path / "provider"
    revision = "a" * 40

    sibling.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "observation = {'path': os.environ['PATH'], 'sibling': str(Path(__file__).resolve())}\n"
        "Path(os.environ['SIBLING_MARKER']).write_text(json.dumps(observation), encoding='ascii')\n",
        encoding="ascii",
    )
    sibling.chmod(0o755)
    build_tool.write_text(
        f"#!/bin/sh\nexec {sibling_name}\n",
        encoding="ascii",
    )
    build_tool.chmod(0o755)
    provider.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, subprocess, sys\n"
        "request = json.load(sys.stdin)\n"
        "value = request['input']\n"
        "build = subprocess.run([value['build_command'][0]], check=False)\n"
        "if build.returncode != 0:\n"
        "    json.dump({'version': 'fkst.ops.invocation.v1', 'ok': False, "
        "'failure': {'code': 'BUILD_FAILED', 'message': 'declared build tool could not reach sibling', 'details': {}}}, sys.stdout)\n"
        "    raise SystemExit(1)\n"
        "binary = pathlib.Path(value['engine_binary'])\n"
        "binary.write_text('#!/bin/sh\\nexit 0\\n', encoding='ascii')\n"
        "binary.chmod(0o755)\n"
        "json.dump({'version': 'fkst.ops.invocation.v1', 'ok': True, "
        "'result': {'binary': str(binary), 'source_rev': value['expected_revision']}}, sys.stdout)\n",
        encoding="ascii",
    )
    provider.chmod(0o755)

    profile = tmp_path / "profile.toml"
    profile_tools = tomllib.loads(profile.read_text(encoding="ascii"))["tools"]
    assert profile_tools[build_tool_name] == str(build_tool)
    assert sibling_name not in profile_tools

    source = OPERATOR.read_text(encoding="utf-8")
    invoke_provider_pattern = re.compile(r"^invoke_provider\(\) \{", re.MULTILINE)
    invoke_provider_definitions = list(invoke_provider_pattern.finditer(source))
    assert len(invoke_provider_definitions) == 1, (
        "operator must contain exactly one definition matched by the anchored "
        "invoke_provider() pattern; "
        f"found {len(invoke_provider_definitions)}"
    )
    invoke_provider_start = invoke_provider_definitions[0].start()
    invoke_provider = source[
        invoke_provider_start:
        source.index("\nresolve_github_writer()", invoke_provider_start)
    ]
    invoke_engine_start = source.index("invoke_engine_build_provider()")
    invoke_engine = source[
        invoke_engine_start:
        source.index("\nensure_engine_binary_current()", invoke_engine_start)
    ]
    configuration = json.dumps({"build_command": [str(build_tool)]})
    provider_input = json.dumps(
        {
            "engine_checkout": str(tmp_path / "checkout"),
            "engine_binary": str(binary),
            "expected_revision": revision,
            "operation": "build",
            "build_command": [str(build_tool)],
        }
    )
    enclosing_shell_state_marker = tmp_path / "enclosing-shell-state"
    command = f'''set -uo pipefail
PYTHON="{sys.executable}"
_repo_root="{ROOT}"
_self_dir="{ROOT / 'ops'}"
DEPLOYMENT_PYTHON="{sys.executable}"
FKST_OPS_MACHINE_PROFILE="$1"
{_operator_loader()}
{invoke_provider}
{invoke_engine}
ENGINE_CHECKOUT="$2"
BIN="$3"
ENGINE_REVISION="$4"
ENGINE_PROVIDER="$5"
ENGINE_CONTRACT=fkst.ops.engine.v1
ENGINE_PROVIDER_CONFIGURATION="$6"
enclosing_shell_flags_before="$-"
invoke_engine_build_provider
build_status=$?
invoke_provider "$ENGINE_PROVIDER" "$ENGINE_CONTRACT" <<< "$7" >/dev/null
provider_status=$?
printf '%s\n%s\n%s\n' "$PATH" "$enclosing_shell_flags_before" "$-" > "$8"
[ "$build_status" -eq 0 ] || exit "$build_status"
exit "$provider_status"
'''
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            command,
            "test",
            str(profile),
            str(tmp_path / "checkout"),
            str(binary),
            revision,
            str(provider),
            configuration,
            provider_input,
            str(enclosing_shell_state_marker),
        ],
        env={"PATH": str(launcher), "SIBLING_MARKER": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "version": "fkst.ops.invocation.v1",
        "ok": True,
        "result": {"binary": str(binary), "source_rev": revision},
    }
    assert marker.is_file()
    observation = json.loads(marker.read_text(encoding="ascii"))
    assert observation["sibling"] == str(sibling)
    assert observation["path"] == os.pathsep.join(child_path)
    assert str(launcher) not in os.get_exec_path({"PATH": observation["path"]})
    enclosing_path, flags_before, flags_after = (
        enclosing_shell_state_marker.read_text(encoding="ascii").splitlines()
    )
    assert enclosing_path == str(launcher), (
        "invoke_provider leaked DEPLOYMENT_CHILD_PATH into the enclosing operator shell"
    )
    assert flags_after == flags_before, (
        "invoke_provider changed enclosing shell option flags"
    )
