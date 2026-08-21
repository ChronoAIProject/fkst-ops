"""Shared fixtures for artifact generation tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from bootstrap.canonical_tree import canonical_tree_sha256



ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "watch" / "generate_artifacts.py"
FIXTURES = ROOT / "tests" / "schema" / "fixtures"
GIT = shutil.which("git")
assert GIT is not None


def path_without(root: Path, *excluded: str) -> str:
    """Mirror the ambient executable PATH while omitting exact tool names."""
    directory = root / "selective-path"
    directory.mkdir()
    excluded_names = set(excluded)
    included_names: set[str] = set()
    for path_entry in os.get_exec_path():
        source_directory = Path(path_entry or os.curdir)
        try:
            # Materialise inside the guard. A PATH entry that does not exist is ordinary -
            # a hosted runner ships `~/.local/bin` on PATH without creating it - and on the
            # interpreter this suite runs in CI the failure surfaced from iteration rather
            # than from the call, so guarding only the call caught nothing there while
            # appearing to work on the developer's newer interpreter.
            entries = list(source_directory.iterdir())
        except OSError:
            continue
        for source in entries:
            if source.name in excluded_names or source.name in included_names:
                continue
            if not source.is_file() or not os.access(source, os.X_OK):
                continue
            (directory / source.name).symlink_to(source.absolute())
            included_names.add(source.name)
    return str(directory)


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

    engine = tmp_path / "engine-source"
    engine_pin = source(engine, {
        "bin/build-provider": "#!/bin/sh\nexit 0\n",
        "cargo": "#!/bin/sh\nmkdir -p target/debug\nprintf '#!/bin/sh\\nexit 0\\n' > target/debug/engine\nchmod +x target/debug/engine\n",
    })
    target = tmp_path / "target-source"
    target_pin = source(target, {
        ".control/engine-ref": engine_pin[0] + "\n",
        "packages/github-devloop/entry": "x",
        "packages/github-devloop-pr/entry": "x",
        "packages/github-devloop-integration/entry": "x",
        "providers/engine-board": "#!/bin/sh\nexit 0\n",
        "providers/github-board": "#!/bin/sh\nexit 0\n",
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
                 f'checkout_role = "{checkout_role}"\n')
        if checkout_role == "mechanism":
            lock += (f'[external_source.resolved]\nrev = "{pin[0]}"\n'
                     f'tree_sha256 = "{pin[1]}"\n')
        lock += "\n"
    (repository / "fkst.lock").write_text(lock)
    return repository, home, tomllib.loads((repository / "deployment.toml").read_text())


def run_generator(
    repository: Path, home: Path, machine_root: Path | None = None,
    bot_login: str | None = "fkst-bot", github_credential_source: str | None = None,
    integration_branch: str | None = None,
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
            ])
            if entry["checkout_role"] == "mechanism":
                lines.extend([
                    "[external_source.resolved]", f'rev = "{entry["resolved"]["rev"]}"',
                    f'tree_sha256 = "{entry["resolved"]["tree_sha256"]}"',
                ])
            lines.append("")
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
    environment.pop("GH_TOKEN", None)
    environment.update({
        "FKST_LAUNCHCTL": str(launchctl),
        "LAUNCHCTL_STATE": str(home / "launchctl.state"),
        "LAUNCHCTL_CALLS": str(home / "launchctl.calls"),
    })
    command = [sys.executable, str(mechanism / "watch" / "generate_artifacts.py"), str(repository)]
    if bot_login is not None:
        command.extend(["--bot-login", bot_login])
    if github_credential_source is not None:
        command.extend(["--github-credential-source", github_credential_source])
    if machine_root is not None:
        command.extend(["--machine-state-root", str(machine_root)])
    if integration_branch is not None:
        command.extend(["--integration-branch", integration_branch])
    return subprocess.run(
        command, env=environment,
        text=True, capture_output=True, check=False,
    )
