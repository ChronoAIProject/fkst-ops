import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "providers" / "board_engine_durable.py"


def invoke(payload):
    return subprocess.run([str(PROVIDER)], input=json.dumps(payload), text=True, stdout=subprocess.PIPE, check=False)


def test_contract_failure_is_typed():
    result = invoke({"version": "wrong", "contract": "fkst.ops.board.engine-durable.v1", "input": {}})
    body = json.loads(result.stdout)
    assert result.returncode == 2
    assert body == {"version": "fkst.ops.invocation.v1", "ok": False, "failure": {"code": "INVALID_INPUT", "message": "invocation version or contract mismatch", "details": {}}}


def test_observe_success_is_typed(tmp_path):
    engine = tmp_path / "engine"
    engine.write_text("#!/bin/sh\nprintf '%s\\n' '{\"entities\":[],\"queues\":[],\"avm_scoreboard\":[]}'\n", encoding="utf-8")
    engine.chmod(0o755)
    durable = tmp_path / "durable"
    durable.mkdir()
    payload = {"version": "fkst.ops.invocation.v1", "contract": "fkst.ops.board.engine-durable.v1", "input": {
        "engine_binary": str(engine), "durable_root": str(durable), "cache": str(tmp_path / "cache.json"),
        "refresh": True, "ttl_seconds": 60, "stall_seconds": 1800,
    }}
    result = invoke(payload)
    body = json.loads(result.stdout)
    assert result.returncode == 0
    assert body["ok"] is True
    assert body["result"]["view"] == "engine-durable"
    assert body["result"]["health"] == {"status": "HEALTHY", "anomalies": []}
