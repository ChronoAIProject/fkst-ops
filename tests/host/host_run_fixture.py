#!/usr/bin/env python3
"""Shared fixtures for host_run.sh behavior tests."""

from __future__ import annotations

import os
import json
import signal
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class HostRunHarness:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.substrate_host = self.root / "substrate-host"
        self.website_host = self.root / "website-host"
        self.durable = self.root / "durable"
        self.runtime = self.root / "runtime"
        platform_files = {
            f"packages/{pkg}/fkst.toml": f'kind = "package"\nname = "{pkg}"\n'
            for pkg in ("github-proxy", "consensus")
        }
        self.platform, _ = create_git_source(self.root, "platform", platform_files)
        packages_host_files = dict(platform_files)
        packages_host_files["packages/autochrono/fkst.toml"] = (
            'kind = "package"\nname = "autochrono"\n'
        )
        self.packages_host, _ = create_git_source(
            self.root,
            "packages-host",
            packages_host_files,
        )
        (self.website_host / ".fkst" / "local-packages" / "site-board").mkdir(parents=True)
        self.substrate_host.mkdir()

    def close(self) -> None:
        self.tmp.cleanup()

    def run_helper(self, body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", "-c", body],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def package_roots(self, command_args: list[str]) -> subprocess.CompletedProcess[str]:
        quoted = " ".join(shell_quote(arg) for arg in command_args)
        return self.run_helper(
            textwrap.dedent(
                f"""\
                set -euo pipefail
                source host/host_run.sh
                host_run_parse_supervise_args {quoted}
                host_run_validate_shape
                host_run_build_package_roots
                host_run_print_package_roots
                """
            )
        )

    def write_external_sources_lock(self, entries: list[tuple[str, Path, str]], *, root: Path | None = None) -> None:
        target_root = root or self.website_host
        (target_root / "fkst.lock").write_text(
            "\n".join(
                textwrap.dedent(
                    f"""\
                    [[external_source]]
                    id = {json.dumps(source_id)}
                    git = {json.dumps(str(repo))}

                    [external_source.resolved]
                    rev = {json.dumps(rev)}
                    """
                )
                for source_id, repo, rev in entries
            )
            + "\n",
            encoding="utf-8",
        )

    def write_workspace_manifest(
        self,
        *,
        root: Path | None = None,
        workspace_units: list[str] | None = None,
        workspace_packages: list[str] | None = None,
        external_sources: list[tuple[str, Path, list[str]]] | None = None,
    ) -> None:
        target_root = root or self.website_host
        units = workspace_units or [".fkst/local-packages/*"]
        chunks = [f"[workspace]\nunits = {json.dumps(units)}\n"]
        for package in workspace_packages or []:
            chunks.append(
                textwrap.dedent(
                    f"""\
                    [[package]]
                    name = {json.dumps(package)}
                    source = "workspace"
                    version = "workspace"
                    """
                )
            )
        for source_id, repo, packages in external_sources or []:
            chunks.append(
                textwrap.dedent(
                    f"""\
                    [[external_sources]]
                    id = {json.dumps(source_id)}
                    git = {json.dumps(str(repo))}
                    packages = {json.dumps(packages)}
                    """
                )
            )
        (target_root / "fkst.workspace.toml").write_text("".join(chunks), encoding="utf-8")


def shell_quote(value: str | Path) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\\''") + "'"


def run_argv(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def create_git_source(root: Path, name: str, files: dict[str, str]) -> tuple[Path, str]:
    repo = root / name
    repo.mkdir(parents=True)
    result = run_argv(["git", "init", "-q"], cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    for key, value in {
        "user.email": "host-run-test@example.invalid",
        "user.name": "Host Run Test",
    }.items():
        result = run_argv(["git", "config", key, value], cwd=repo)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    result = run_argv(["git", "add", "."], cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    result = run_argv(["git", "commit", "-q", "-m", "seed"], cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    result = run_argv(["git", "rev-parse", "HEAD"], cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return repo, result.stdout.strip()


def commit_git_file(repo: Path, rel: str, content: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    for args in (["git", "add", rel], ["git", "commit", "-q", "-m", "advance"]):
        result = run_argv(args, cwd=repo)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
    result = run_argv(["git", "rev-parse", "HEAD"], cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_for_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_is_alive(pid)


def start_orphan_sleep(seconds: int = 60) -> int:
    result = subprocess.run(
        ["/bin/sh", "-c", f"sleep {seconds} >/dev/null 2>&1 & echo $!"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return int(result.stdout.strip())


def kill_if_alive(pid: int) -> None:
    if not pid_is_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    wait_for_dead(pid)
