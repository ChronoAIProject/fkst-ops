#!/usr/bin/env python3
"""Composition checks for the deployment child's generated PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from schema.mechanism_tools import MECHANISM_TOOLS, MechanismTool
from watch.generate_artifacts import _discover_tools, _profile_text

ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
SYSTEM_WHICH = shutil.which


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
    source = OPERATOR.read_text(encoding="utf-8")
    start = source.index("MECHANISM_TOOL_ASSIGNMENTS=")
    end_marker = 'eval "$MECHANISM_TOOL_ASSIGNMENTS"'
    end = source.index(end_marker, start) + len(end_marker)
    loader = source[start:end]
    command = f'''PYTHON="{sys.executable}"
_repo_root="{ROOT}"
_self_dir="{ROOT / 'ops'}"
DEPLOYMENT_PYTHON="{sys.executable}"
FKST_OPS_MACHINE_PROFILE="$1"
{loader}
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
    profile.write_text(_profile_text([], tmp_path, discovered), encoding="ascii")
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
    assert SYSTEM_WHICH("python3", path=composed) == sys.executable
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
