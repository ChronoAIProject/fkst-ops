"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest


@pytest.fixture
def fabricated_mechanism_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    tool_directory = tmp_path / "mechanism-tools"
    tool_directory.mkdir()
    executables: dict[str, str] = {}
    for name in ("codex", "gh", "gh-app"):
        executable = tool_directory / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)
        executables[name] = str(executable)

    system_which = shutil.which

    def which(name: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        if path is None and name in executables:
            return executables[name]
        return system_which(name, mode=mode, path=path)

    monkeypatch.setattr(shutil, "which", which)
    original_path = os.environ.get("PATH")
    path = str(tool_directory)
    if original_path:
        path += os.pathsep + original_path
    monkeypatch.setenv("PATH", path)
    return executables
