from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib
import unittest

from bootstrap.advance_pin import _updated_text


ROOT = Path(__file__).resolve().parents[2]


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def test_advanced_pin_is_observed_and_accepted_by_entry_point(tmp_path: Path) -> None:
    source = tmp_path / "source"
    deployment = tmp_path / "deployment"
    source.mkdir()
    deployment.mkdir()
    for directory in ("bin", "ops", "schema"):
        (source / directory).mkdir()
    shutil.copy2(ROOT / "bin" / "fkst-ops", source / "bin" / "fkst-ops")
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
    assert pin == {"rev": observed_revision}
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


def test_pin_without_legacy_tree_sha256_can_still_be_advanced(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "tracked.txt").write_text("fixture\n", encoding="ascii")
    assert run("git", "init", "-q", cwd=source).returncode == 0
    assert run("git", "config", "user.email", "test@example.invalid", cwd=source).returncode == 0
    assert run("git", "config", "user.name", "Test", cwd=source).returncode == 0
    assert run("git", "add", ".", cwd=source).returncode == 0
    assert run("git", "commit", "-qm", "fixture", cwd=source).returncode == 0
    revision = run("git", "rev-parse", "HEAD", cwd=source).stdout.strip()
    lock = tmp_path / "fkst.lock"
    lock.write_text(
        f'[[external_source]]\nid = "source"\ngit = "{source}"\ncheckout_role = "mechanism"\n'
        f'[external_source.resolved]\nrev = "{"0" * 40}"\n',
        encoding="ascii",
    )

    advanced = run(
        str(ROOT / "bin" / "fkst-pin"), "--lock", str(lock), "--source", "source",
        "--revision", revision, cwd=tmp_path,
    )

    assert advanced.returncode == 0, advanced.stderr
    resolved = tomllib.loads(lock.read_text(encoding="ascii"))["external_source"][0]["resolved"]
    assert resolved == {"rev": revision}


class UpdatedTextTests(unittest.TestCase):
    def test_removes_legacy_tree_sha256_toml_variants(self) -> None:
        legacy_value = "sha256-" + "a" * 64
        sibling_value = "sha256-" + "b" * 64
        cases = {
            "indented": f'    tree_sha256 = "{legacy_value}"',
            "inline-comment": f'tree_sha256 = "{legacy_value}" # legacy pin',
            "single-quoted": f"tree_sha256 = '{legacy_value}'",
            "single-quoted-inline-comment": (
                f"    tree_sha256 = '{legacy_value}' # legacy pin"
            ),
        }
        for name, legacy_line in cases.items():
            with self.subTest(name=name):
                text = (
                    '[[external_source]]\n'
                    'id = "target"\n'
                    'git = "target.git"\n'
                    'checkout_role = "mechanism"\n'
                    '[external_source.resolved]\n'
                    f'rev = "{"0" * 40}"\n'
                    f'{legacy_line}\n'
                    '[[external_source]]\n'
                    'id = "sibling"\n'
                    'git = "sibling.git"\n'
                    'checkout_role = "mechanism"\n'
                    '[external_source.resolved]\n'
                    f'rev = "{"1" * 40}"\n'
                    f'tree_sha256 = "{sibling_value}"\n'
                )

                updated = _updated_text(text, "target", "2" * 40)
                entries = tomllib.loads(updated)["external_source"]

                self.assertEqual(entries[0]["resolved"], {"rev": "2" * 40})
                self.assertEqual(
                    entries[1]["resolved"],
                    {"rev": "1" * 40, "tree_sha256": sibling_value},
                )

    def test_unsupported_legacy_tree_sha256_spellings_fail_loudly(self) -> None:
        legacy_value = "sha256-" + "a" * 64
        cases = {
            "multiline-basic": f'tree_sha256 = """\n{legacy_value}\n"""',
            "multiline-literal": f"tree_sha256 = '''\n{legacy_value}\n'''",
            "quoted-basic-key": f'"tree_sha256" = "{legacy_value}"',
            "quoted-literal-key": f"'tree_sha256' = '{legacy_value}'",
        }
        for name, legacy_assignment in cases.items():
            with self.subTest(name=name):
                text = (
                    '[[external_source]]\n'
                    'id = "target"\n'
                    'git = "target.git"\n'
                    'checkout_role = "mechanism"\n'
                    '[external_source.resolved]\n'
                    f'rev = "{"0" * 40}"\n'
                    f'{legacy_assignment}\n'
                )

                with self.assertRaisesRegex(ValueError, "unsupported TOML spelling"):
                    _updated_text(text, "target", "2" * 40)
