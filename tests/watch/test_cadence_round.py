from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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


def run_round(repository: Path, profile: Path, ledger: Path, operator: Path, env: dict[str, str]):
    return subprocess.run(
        [
            sys.executable,
            str(ROUND),
            "--deployment-repository",
            str(repository),
            "--machine-profile",
            str(profile),
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
