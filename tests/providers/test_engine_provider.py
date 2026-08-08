from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "providers" / "engine.py"


def command(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def repository(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    command("git", "init", "--bare", str(remote))
    command("git", "init", "-b", "build", str(seed))
    command("git", "config", "user.email", "test@example.invalid", cwd=seed)
    command("git", "config", "user.name", "Test", cwd=seed)
    (seed / "README").write_text("fixture\n", encoding="ascii")
    command("git", "add", "README", cwd=seed)
    command("git", "commit", "-m", "fixture", cwd=seed)
    command("git", "remote", "add", "origin", str(remote), cwd=seed)
    command("git", "push", "-u", "origin", "build", cwd=seed)
    command("git", "symbolic-ref", "HEAD", "refs/heads/build", cwd=remote)
    command("git", "clone", str(remote), str(checkout))
    return checkout


def invoke(payload: object) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [str(PROVIDER)], input=json.dumps(payload), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    return result, json.loads(result.stdout)


def payload(checkout: Path, binary: Path, build_command: list[str], branch: str = "build") -> dict:
    return {
        "version": "fkst.ops.invocation.v1",
        "contract": "fkst.ops.engine.v1",
        "input": {
            "engine_checkout": str(checkout),
            "engine_binary": str(binary),
            "expected_branch": branch,
            "operation": "build",
            "build_command": build_command,
        },
    }


def test_build_success(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    binary = checkout / "out" / "engine"
    build = checkout / "build.py"
    build.write_text(
        "#!/usr/bin/env python3\nfrom pathlib import Path\np=Path('out/engine')\np.parent.mkdir()\np.write_text('#!/bin/sh\\n')\np.chmod(0o755)\n",
        encoding="ascii",
    )
    build.chmod(0o755)
    result, body = invoke(payload(checkout, binary, [str(build)]))
    assert result.returncode == 0
    assert body == {
        "version": "fkst.ops.invocation.v1",
        "ok": True,
        "result": {"binary": str(binary), "source_rev": command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()},
    }


def test_missing_checkout_is_typed(tmp_path: Path) -> None:
    result, body = invoke(payload(tmp_path / "missing", tmp_path / "engine", ["true"]))
    assert result.returncode == 1
    assert body["failure"]["code"] == "CHECKOUT_MISSING"


def test_wrong_branch_is_typed(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    result, body = invoke(payload(checkout, checkout / "engine", ["true"], branch="other"))
    assert result.returncode == 1
    assert body["failure"]["code"] == "WRONG_BRANCH"


def test_failed_build_is_typed(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    result, body = invoke(payload(checkout, checkout / "engine", ["false"]))
    assert result.returncode == 1
    assert body["failure"]["code"] == "BUILD_FAILED"


def test_malformed_input_has_exact_failure_shape() -> None:
    result, body = invoke({"version": "fkst.ops.invocation.v1", "contract": "fkst.ops.engine.v1", "input": {}})
    assert result.returncode == 2
    assert body == {
        "version": "fkst.ops.invocation.v1",
        "ok": False,
        "failure": {"code": "INVALID_INPUT", "message": "input must contain exactly the engine fields", "details": {}},
    }
