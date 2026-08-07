import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def provider(tmp_path: Path, name: str, healthy: bool, body=None) -> str:
    path = tmp_path / name
    if body is not None:
        code = 0
    elif healthy:
        body = {"version": "fkst.ops.invocation.v1", "ok": True, "result": {"view": name, "rows": [{"key": "one", "classification": "ok", "fields": {"text": f"{name} healthy"}}]}}
        if name == "engine-durable":
            body["result"]["health"] = {"status": "HEALTHY", "anomalies": []}
        code = 0
    else:
        body = {"version": "fkst.ops.invocation.v1", "ok": False, "failure": {"code": "FETCH_FAILED", "message": "fixture failure", "details": {}}}
        code = 1
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{json.dumps(body)}'\nexit {code}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def run_case(tmp_path: Path, github_ok: bool, engine_ok: bool):
    github_input = tmp_path / "github.json"
    engine_input = tmp_path / "engine.json"
    github_input.write_text("{}", encoding="utf-8")
    engine_input.write_text("{}", encoding="utf-8")
    return subprocess.run([
        sys.executable, str(ROOT / "board" / "board.py"),
        "--github-provider", provider(tmp_path, "github-control", github_ok),
        "--engine-provider", provider(tmp_path, "engine-durable", engine_ok),
        "--github-input", str(github_input), "--engine-input", str(engine_input),
    ], text=True, stdout=subprocess.PIPE, check=False)


def test_both_healthy(tmp_path):
    result = run_case(tmp_path, True, True)
    assert result.returncode == 0
    assert "github-control healthy" in result.stdout
    assert "engine-durable healthy" in result.stdout


def test_github_failure_keeps_engine(tmp_path):
    result = run_case(tmp_path, False, True)
    assert result.returncode != 0
    assert "FAIL github-control" in result.stdout
    assert "engine-durable healthy" in result.stdout


def test_engine_failure_keeps_github(tmp_path):
    result = run_case(tmp_path, True, False)
    assert result.returncode != 0
    assert "github-control healthy" in result.stdout
    assert "FAIL engine-durable" in result.stdout


def test_both_failing(tmp_path):
    result = run_case(tmp_path, False, False)
    assert result.returncode != 0
    assert "FAIL github-control" in result.stdout
    assert "FAIL engine-durable" in result.stdout


def test_malformed_board_row_fails_closed_and_exits_nonzero(tmp_path):
    github_input = tmp_path / "github.json"
    engine_input = tmp_path / "engine.json"
    github_input.write_text("{}", encoding="utf-8")
    engine_input.write_text("{}", encoding="utf-8")
    malformed = {"version": "fkst.ops.invocation.v1", "ok": True, "result": {
        "view": "github-control", "rows": [{"key": 7, "classification": "ok", "fields": []}],
    }}
    result = subprocess.run([
        sys.executable, str(ROOT / "board" / "board.py"),
        "--github-provider", provider(tmp_path, "github-control", True, malformed),
        "--engine-provider", provider(tmp_path, "engine-durable", True),
        "--github-input", str(github_input), "--engine-input", str(engine_input),
    ], text=True, stdout=subprocess.PIPE, check=False)
    assert result.returncode != 0
    assert "FAIL github-control: invalid provider output" in result.stdout


def test_engine_empty_result_fails_selected_contract(tmp_path):
    executable = tmp_path / "provider"
    body = {"version": "fkst.ops.invocation.v1", "ok": True, "result": {}}
    executable.write_text(f"#!/bin/sh\nprintf '%s\\n' '{json.dumps(body)}'\n", encoding="utf-8")
    executable.chmod(0o755)
    result = subprocess.run([
        sys.executable, str(ROOT / "ops" / "invoke_provider.py"), str(executable), "fkst.ops.engine.v1",
    ], input="{}", text=True, capture_output=True)
    assert result.returncode != 0
    assert "engine result has missing or unknown members" in result.stderr
