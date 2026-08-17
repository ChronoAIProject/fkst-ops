from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def test_advanced_pin_is_observed_and_accepted_by_entry_point(tmp_path: Path) -> None:
    source = tmp_path / "source"
    deployment = tmp_path / "deployment"
    source.mkdir()
    deployment.mkdir()
    for directory in ("bin", "bootstrap", "ops", "schema"):
        (source / directory).mkdir()
    shutil.copy2(ROOT / "bin" / "fkst-ops", source / "bin" / "fkst-ops")
    shutil.copy2(ROOT / "bootstrap" / "canonical_tree.py", source / "bootstrap" / "canonical_tree.py")
    shutil.copy2(ROOT / "ops" / "public_actions.sh", source / "ops" / "public_actions.sh")
    operator = source / "ops" / "deployment_operator.sh"
    operator.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    operator.chmod(operator.stat().st_mode | stat.S_IXUSR)
    (source / "schema" / "__init__.py").write_text("", encoding="ascii")
    (source / "schema" / "validator.py").write_text("print('{}')\n", encoding="ascii")
    assert run("git", "init", "-q", cwd=source).returncode == 0
    assert run("git", "config", "user.email", "test@example.invalid", cwd=source).returncode == 0
    assert run("git", "config", "user.name", "Test", cwd=source).returncode == 0
    assert run("git", "add", ".", cwd=source).returncode == 0
    assert run("git", "commit", "-qm", "fixture", cwd=source).returncode == 0
    observed_revision = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    observed_tree = run(
        "python3", str(ROOT / "bootstrap" / "canonical_tree.py"), str(source), observed_revision,
        cwd=tmp_path,
    ).stdout.strip()
    lock = deployment / "fkst.lock"
    lock.write_text(
        f'[[external_source]]\nid = "fkst-ops"\ngit = "{source}"\ncheckout_role = "mechanism"\n'
        f'[external_source.resolved]\nrev = "{"0" * 40}"\n'
        f'tree_sha256 = "sha256-{"0" * 64}"\n'
        f'[[external_source]]\nid = "other"\ngit = "{source}"\ncheckout_role = "mechanism"\n'
        f'[external_source.resolved]\nrev = "{"1" * 40}"\n'
        f'tree_sha256 = "sha256-{"1" * 64}"\n',
        encoding="utf-8",
    )

    advanced = run(
        str(ROOT / "bin" / "fkst-pin"), "--lock", str(lock), "--source", "fkst-ops",
        "--revision", observed_revision, cwd=deployment,
    )
    assert advanced.returncode == 0, advanced.stderr
    pin = tomllib.loads(lock.read_text())["external_source"][0]["resolved"]
    assert pin == {"rev": observed_revision, "tree_sha256": observed_tree}
    other = tomllib.loads(lock.read_text())["external_source"][1]["resolved"]
    assert other == {"rev": "1" * 40, "tree_sha256": "sha256-" + "1" * 64}

    environment = {**os.environ, "FKST_OPS_CACHE_ROOT": str(tmp_path / "cache")}
    verified = run(
        "bash", str(ROOT / "bin" / "fkst-ops"), "preflight", "--deployment-dir", str(deployment),
        "--declaration", "declaration.toml", "--machine-config", "machine.toml",
        cwd=deployment, env=environment,
    )
    assert verified.returncode == 0, verified.stderr


def test_deployment_operated_source_pin_cannot_be_advanced(tmp_path: Path) -> None:
    lock = tmp_path / "fkst.lock"
    original = (
        '[[external_source]]\n'
        'id = "target"\n'
        'git = "https://invalid.example/target.git"\n'
        'checkout_role = "deployment-operated"\n'
        '[external_source.resolved]\n'
        f'rev = "{"1" * 40}"\n'
        f'tree_sha256 = "sha256-{"1" * 64}"\n'
    )
    lock.write_text(original, encoding="ascii")

    result = run(
        str(ROOT / "bin" / "fkst-pin"),
        "--lock",
        str(lock),
        "--source",
        "target",
        "--revision",
        "2" * 40,
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "only mechanism source pins can be advanced" in result.stderr
    assert lock.read_text(encoding="ascii") == original
