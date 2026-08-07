import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "providers" / "board_github_control.sh"


def test_contract_failure_is_typed():
    result = subprocess.run([str(PROVIDER)], input="{}", text=True, stdout=subprocess.PIPE, check=False)
    body = json.loads(result.stdout)
    assert result.returncode == 2
    assert body["version"] == "fkst.ops.invocation.v1"
    assert body["failure"]["code"] == "INVALID_INPUT"
    assert body["failure"]["details"] == {}


def test_platform_checkout_is_an_explicit_input(tmp_path):
    request = {
        "version": "fkst.ops.invocation.v1",
        "contract": "fkst.ops.board.github-control.v1",
        "input": {
            "target_identity": "owner/repo",
            "profile": {"platform_checkout": str(tmp_path)},
            "bot_login": "bot",
            "managed_bot_set": [],
        },
    }
    result = subprocess.run(
        [str(PROVIDER)], input=json.dumps(request), text=True, stdout=subprocess.PIPE, check=False
    )
    body = json.loads(result.stdout)
    assert result.returncode == 2
    assert body["failure"]["code"] == "INVALID_INPUT"
