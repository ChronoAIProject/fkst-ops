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
        failure_code = "OBSERVE_FAILED" if name == "engine-durable" else "FETCH_FAILED"
        body = {"version": "fkst.ops.invocation.v1", "ok": False, "failure": {"code": failure_code, "message": "fixture failure", "details": {}}}
        code = 1
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{json.dumps(body)}'\nexit {code}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def run_board(
    tmp_path: Path,
    *,
    github_provider: str | None = None,
    github_input: Path | None = None,
    engine_provider: str | None = None,
    engine_input: Path | None = None,
):
    if engine_provider is None:
        engine_provider = provider(tmp_path, "engine-durable", True)
    if engine_input is None:
        engine_input = tmp_path / "engine.json"
        engine_input.write_text("{}", encoding="utf-8")
    command = [sys.executable, str(ROOT / "board" / "board.py")]
    if github_provider is not None:
        command.extend(["--github-provider", github_provider])
    if github_input is not None:
        command.extend(["--github-input", str(github_input)])
    command.extend(["--engine-provider", engine_provider, "--engine-input", str(engine_input)])
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def test_healthy_engine_renders_missing_github_and_exits_zero(tmp_path):
    result = run_board(tmp_path)
    assert result.returncode == 0
    assert "[github-control]\nMISSING github-control: plane is not implemented\n" in result.stdout
    assert "engine-durable healthy" in result.stdout
    assert "FAIL github-control" not in result.stdout


def test_failing_engine_renders_missing_github_and_fail_and_exits_nonzero(tmp_path):
    result = run_board(
        tmp_path, engine_provider=provider(tmp_path, "engine-durable", False)
    )
    assert result.returncode != 0
    assert "MISSING github-control: plane is not implemented" in result.stdout
    assert "FAIL engine-durable: OBSERVE_FAILED: fixture failure" in result.stdout


def test_malformed_engine_row_fails_closed_and_exits_nonzero(tmp_path):
    malformed = {"version": "fkst.ops.invocation.v1", "ok": True, "result": {
        "view": "engine-durable", "rows": [{"key": 7, "classification": "ok", "fields": []}],
        "health": {"status": "HEALTHY", "anomalies": []},
    }}
    result = run_board(
        tmp_path,
        engine_provider=provider(tmp_path, "engine-durable", True, malformed),
    )
    assert result.returncode != 0
    assert "MISSING github-control: plane is not implemented" in result.stdout
    assert "FAIL engine-durable: invalid provider output" in result.stdout


def test_github_compatibility_arguments_are_inert(tmp_path):
    marker = tmp_path / "github-called"
    executable = tmp_path / "github-control"
    executable.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    executable.chmod(0o755)
    github_input = tmp_path / "github.json"
    github_input.write_text("not json", encoding="utf-8")
    result = run_board(
        tmp_path, github_provider=str(executable), github_input=github_input
    )
    assert result.returncode == 0
    assert "MISSING github-control: plane is not implemented" in result.stdout
    assert not marker.exists()


def test_missing_github_input_is_ignored(tmp_path):
    missing_input = tmp_path / "does-not-exist.json"
    result = run_board(
        tmp_path,
        github_provider=str(tmp_path / "does-not-exist-provider"),
        github_input=missing_input,
    )
    assert result.returncode == 0
    assert "MISSING github-control: plane is not implemented" in result.stdout
    assert "engine-durable healthy" in result.stdout


def test_github_compatibility_arguments_are_independently_optional(tmp_path):
    github_input = tmp_path / "github.json"
    github_input.write_text("not json", encoding="utf-8")
    for compatibility_argument in (
        {"github_provider": str(tmp_path / "does-not-exist-provider")},
        {"github_input": github_input},
    ):
        result = run_board(tmp_path, **compatibility_argument)
        assert result.returncode == 0
        assert "MISSING github-control: plane is not implemented" in result.stdout
        assert "engine-durable healthy" in result.stdout


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
