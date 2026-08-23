#!/usr/bin/env python3
"""Health-status coverage for durable dead letters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"


def write_engine(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="ascii")
    path.chmod(0o755)


def run_status(tmp_path: Path, snapshot: object | None, engine_body: str | None = None) -> subprocess.CompletedProcess[str]:
    log = tmp_path / "supervise.log"
    log.write_text(
        "FKST_GITHUB_WRITE=1 FKST_GITHUB_WRITER_LOGIN=resolved-bot "
        "FKST_GITHUB_CLAIM_MODE=label FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=0\n"
        "last event\n",
        encoding="ascii",
    )
    durable = tmp_path / "durable"
    durable.mkdir()
    engine = tmp_path / "fkst-framework"
    snapshot_path = tmp_path / "snapshot.json"
    if snapshot is not None:
        snapshot_path.write_text(json.dumps(snapshot), encoding="ascii")
    write_engine(
        engine,
        engine_body
        or '[ "$1" = observe ] && [ "$2" = --durable-root ] && '
        '[ "$3" = "$EXPECTED_DURABLE" ] && [ "$4" = --json ] || exit 64\n'
        'cat "$SNAPSHOT"',
    )
    command = f'''PYTHON="{sys.executable}"
_self_dir="{ROOT / 'ops'}"
eval "$(sed -n '/^dead_letter_count()/,/^}}/p' "{OPERATOR}")"
eval "$(sed -n '/^status_one()/,/^}}/p' "{OPERATOR}")"
cfg() {{ HOST=/host; PKGSRC=/platform; REPO=example/repo; BIN="$ENGINE"; DUR="$DURABLE"; }}
pidof_df() {{ echo 123; }}; latest_log() {{ echo "$LOG"; }}
fmt_uptime() {{ echo 1m00s; }}; engine_panic_count() {{ echo 0; }}
ps() {{ echo 00:01:00; }}; git() {{ echo abcdef123456; }}
status_one fixture
'''
    return subprocess.run(
        ["bash", "-c", command],
        env={
            **os.environ,
            "DURABLE": str(durable),
            "ENGINE": str(engine),
            "EXPECTED_DURABLE": str(durable),
            "LOG": str(log),
            "SNAPSHOT": str(snapshot_path),
        },
        text=True,
        capture_output=True,
        check=False,
    )


def observe_snapshot(dead_letters: list[object], *, truncated: bool = False) -> dict[str, object]:
    return {
        "truncated": {"dead_letters": truncated},
        "dead_letters": dead_letters,
    }


def test_dead_letters_make_status_unhealthy_without_changing_auth_exit_contract(tmp_path: Path) -> None:
    result = run_status(tmp_path, observe_snapshot([{"delivery_id": "delivery-1"}]))

    assert result.returncode == 0, result.stderr
    assert "health=UNHEALTHY" in result.stdout
    assert "auth-fail=0 dead=1" in result.stdout


def test_no_dead_letters_remains_healthy(tmp_path: Path) -> None:
    result = run_status(tmp_path, observe_snapshot([]))

    assert result.returncode == 0, result.stderr
    assert "health=HEALTHY" in result.stdout
    assert "auth-fail=0 dead=0" in result.stdout


def test_unreadable_dead_letter_snapshot_is_explicitly_unknown(tmp_path: Path) -> None:
    result = run_status(tmp_path, None, "exit 74")

    assert result.returncode == 0, result.stderr
    assert "health=UNKNOWN" in result.stdout
    assert "auth-fail=0 dead=unknown" in result.stdout
    assert "dead=0" not in result.stdout


def test_truncated_dead_letter_snapshot_is_explicitly_unknown(tmp_path: Path) -> None:
    result = run_status(
        tmp_path,
        observe_snapshot([{"delivery_id": "delivery-1"}], truncated=True),
    )

    assert result.returncode == 0, result.stderr
    assert "health=UNKNOWN" in result.stdout
    assert "auth-fail=0 dead=unknown" in result.stdout
    assert "dead=0" not in result.stdout


def test_dead_letter_observation_has_a_hard_time_bound(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_status(tmp_path, None, "sleep 5")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 2.0
    assert "health=UNKNOWN" in result.stdout
    assert "dead=unknown" in result.stdout
