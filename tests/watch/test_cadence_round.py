from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]
ROUND = ROOT / "watch" / "cadence_round.py"


def declaration(path: Path, identity: str) -> None:
    path.write_text(
        f'schema = "fkst.ops.deployment.v1"\n[[deployment]]\nid = "{identity}"\n',
        encoding="ascii",
    )


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    repository = tmp_path / "deployment-repository"
    repository.mkdir()
    (repository / "fkst.lock").write_text("", encoding="ascii")
    profile = tmp_path / "machine-profile.toml"
    profile.write_text('schema = "fkst.ops.machine-profile.v1"\n', encoding="ascii")
    ledger = tmp_path / "ledger.jsonl"
    calls = tmp_path / "calls.jsonl"
    operator = tmp_path / "operator"
    operator.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "declaration = sys.argv[sys.argv.index('--declaration') + 1]\n"
        "action = sys.argv[-1]\n"
        "with open(os.environ['CALLS'], 'a') as stream:\n"
        "    stream.write(json.dumps({'deployment': os.path.basename(declaration), 'action': action, 'write': os.environ.get('FKST_GITHUB_WRITE')}) + '\\n')\n"
        "if action == 'status':\n"
        "    print('status ' + os.path.basename(declaration))\n"
        "if action == 'sync' and os.path.basename(declaration) == os.environ.get('FAIL_DECLARATION'):\n"
        "    raise SystemExit(7)\n",
        encoding="ascii",
    )
    operator.chmod(0o755)
    return repository, profile, ledger, calls, operator


def manifest(repository: Path, paths: list[str]) -> Path:
    declaration_set = repository / "deployment-set.json"
    declaration_set.write_text(json.dumps({
        "schema": "fkst.ops.declaration-input.v1", "declarations": paths,
    }), encoding="ascii")
    binding = lambda relative: {
        "path": relative,
        "sha256": hashlib.sha256((repository / relative).read_bytes()).hexdigest(),
    }
    path = repository.parent / "declarations.json"
    path.write_text(json.dumps({
        "schema": "fkst.ops.declaration-set.v2",
        "repository": str(repository.resolve()),
        "input_set": binding("deployment-set.json"),
        "lock": binding("fkst.lock"),
        "declarations": [binding(item) for item in paths],
    }), encoding="ascii")
    return path


def run_round(repository: Path, profile: Path, ledger: Path, operator: Path, env: dict[str, str]):
    declaration_manifest = manifest(
        repository, sorted(path.name for path in repository.glob("*.toml"))
    )
    return subprocess.run(
        [
            sys.executable,
            str(ROUND),
            "--deployment-repository",
            str(repository),
            "--machine-profile",
            str(profile),
            "--declaration-manifest",
            str(declaration_manifest),
            "--ledger",
            str(ledger),
            "--operator-entry",
            str(operator),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_one_round_writes_one_ledger_line_per_declaration(tmp_path: Path) -> None:
    repository, profile, ledger, calls, operator = fixture(tmp_path)
    declaration(repository / "first.toml", "first")
    declaration(repository / "second.toml", "second")
    result = run_round(repository, profile, ledger, operator, {**os.environ, "CALLS": str(calls)})

    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [record["deployment"] for record in records] == ["first.toml", "second.toml"]
    assert [record["sync_exit_status"] for record in records] == [0, 0]
    assert [record["status_line"] for record in records] == ["status first.toml", "status second.toml"]
    assert all(record["timestamp"].endswith("Z") for record in records)


def test_failure_does_not_stop_round_and_sets_exit_status(tmp_path: Path) -> None:
    repository, profile, ledger, calls, operator = fixture(tmp_path)
    declaration(repository / "first.toml", "first")
    declaration(repository / "second.toml", "second")
    env = {**os.environ, "CALLS": str(calls), "FAIL_DECLARATION": "first.toml"}

    result = run_round(repository, profile, ledger, operator, env)

    assert result.returncode == 1
    invoked = [(call["deployment"], call["action"]) for call in map(json.loads, calls.read_text().splitlines())]
    assert invoked == [
        ("first.toml", "sync"),
        ("first.toml", "status"),
        ("second.toml", "sync"),
        ("second.toml", "status"),
    ]
    assert [json.loads(line)["sync_exit_status"] for line in ledger.read_text().splitlines()] == [7, 0]


def test_round_inherits_and_never_sets_write_posture(tmp_path: Path) -> None:
    repository, profile, ledger, calls, operator = fixture(tmp_path)
    declaration(repository / "only.toml", "only")
    environment = {**os.environ, "CALLS": str(calls)}
    environment.pop("FKST_GITHUB_WRITE", None)

    assert run_round(repository, profile, ledger, operator, environment).returncode == 0
    assert all(call["write"] is None for call in map(json.loads, calls.read_text().splitlines()))

    calls.write_text("", encoding="ascii")
    environment["FKST_GITHUB_WRITE"] = "1"
    assert run_round(repository, profile, ledger, operator, environment).returncode == 0
    assert all(call["write"] == "1" for call in map(json.loads, calls.read_text().splitlines()))


def test_round_propagates_its_interpreter_when_path_has_no_python(tmp_path: Path) -> None:
    repository, profile, ledger, calls, _ = fixture(tmp_path)
    declaration(repository / "only.toml", "only")
    operator = tmp_path / "operator"
    operator.write_text(
        "#!/bin/sh\n"
        '"$FKST_OPS_PYTHON" -c \'import sys; print(sys.executable)\'\n',
        encoding="ascii",
    )
    operator.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir()

    result = run_round(
        repository,
        profile,
        ledger,
        operator,
        {**os.environ, "PATH": str(tools), "CALLS": str(calls)},
    )

    assert result.returncode == 0, result.stderr
    assert [json.loads(line)["status_line"] for line in ledger.read_text().splitlines()] == [sys.executable]


def test_round_consumes_only_manifest_and_rejects_cache_or_test_material(tmp_path: Path) -> None:
    repository, profile, ledger, calls, operator = fixture(tmp_path)
    declaration(repository / "adopted.toml", "adopted")
    declaration(repository / "unlisted.toml", "unlisted")
    cache = repository / ".fkst" / "run" / "fkst-ops" / "checkouts" / "pin"
    cache.mkdir(parents=True)
    declaration(cache / "cached.toml", "cached")
    tests = repository / "tests" / "fixtures"
    tests.mkdir(parents=True)
    declaration(tests / "fixture.toml", "fixture")

    adopted = manifest(repository, ["adopted.toml"])
    command = [
        sys.executable, str(ROUND), "--deployment-repository", str(repository),
        "--machine-profile", str(profile), "--declaration-manifest", str(adopted),
        "--ledger", str(ledger), "--operator-entry", str(operator),
    ]
    result = subprocess.run(
        command, env={**os.environ, "CALLS": str(calls)}, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert {call["deployment"] for call in map(json.loads, calls.read_text().splitlines())} == {"adopted.toml"}

    for forbidden in (".fkst/run/fkst-ops/checkouts/pin/cached.toml", "tests/fixtures/fixture.toml"):
        manifest(repository, [forbidden])
        rejected = subprocess.run(
            command, env={**os.environ, "CALLS": str(calls)}, text=True, capture_output=True
        )
        assert rejected.returncode == 2
        assert "forbidden canonical declaration target" in rejected.stderr or "forbidden declaration manifest path" in rejected.stderr


def test_round_rejects_repository_root_symlink_to_test_fixture(tmp_path: Path) -> None:
    repository, profile, ledger, calls, operator = fixture(tmp_path)
    hidden = repository / "tests" / "fixtures" / "hidden.toml"
    hidden.parent.mkdir(parents=True)
    declaration(hidden, "hidden")
    (repository / "adopted.toml").symlink_to(hidden.relative_to(repository))
    adopted = manifest(repository, ["adopted.toml"])
    result = subprocess.run([
        sys.executable, str(ROUND), "--deployment-repository", str(repository),
        "--machine-profile", str(profile), "--declaration-manifest", str(adopted),
        "--ledger", str(ledger), "--operator-entry", str(operator),
    ], env={**os.environ, "CALLS": str(calls)}, text=True, capture_output=True)
    assert result.returncode == 2
    assert "forbidden canonical declaration target" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize("changed", ["input-set", "declaration", "lock"])
def test_round_rejects_generated_manifest_when_bound_input_changes(
    tmp_path: Path, changed: str
) -> None:
    repository, profile, ledger, calls, operator = fixture(tmp_path)
    declaration(repository / "adopted.toml", "adopted")
    adopted = manifest(repository, ["adopted.toml"])
    targets = {
        "input-set": repository / "deployment-set.json",
        "declaration": repository / "adopted.toml",
        "lock": repository / "fkst.lock",
    }
    with targets[changed].open("a", encoding="ascii") as stream:
        stream.write("\n")
    result = subprocess.run([
        sys.executable, str(ROUND), "--deployment-repository", str(repository),
        "--machine-profile", str(profile), "--declaration-manifest", str(adopted),
        "--ledger", str(ledger), "--operator-entry", str(operator),
    ], env={**os.environ, "CALLS": str(calls)}, text=True, capture_output=True)
    assert result.returncode == 2
    assert "generated manifest is stale" in result.stderr
    assert not calls.exists()
