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


def advance(seed: Path, branch: str, message: str) -> str:
    command("git", "switch", branch, cwd=seed)
    command("git", "commit", "--allow-empty", "-m", message, cwd=seed)
    command("git", "push", "origin", branch, cwd=seed)
    return command("git", "rev-parse", "HEAD", cwd=seed).stdout.strip()


def build_script(checkout: Path) -> tuple[Path, list[str]]:
    binary = checkout / "target" / "debug" / "engine"
    build = checkout / "cargo"
    build.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "p=Path('target/debug/engine')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text('#!/bin/sh\\n')\n"
        "p.chmod(0o755)\n",
        encoding="ascii",
    )
    build.chmod(0o755)
    return binary, [str(build), "build", "-p", "engine"]


def invoke(payload: object) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [str(PROVIDER)], input=json.dumps(payload), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    return result, json.loads(result.stdout)


def branch_payload(checkout: Path, binary: Path, build_command: list[str]) -> dict:
    return {
        "version": "fkst.ops.invocation.v1",
        "contract": "fkst.ops.engine.v1",
        "input": {
            "engine_checkout": str(checkout),
            "engine_binary": str(binary),
            "expected_branch": "build",
            "operation": "build",
            "build_command": build_command,
        },
    }


def payload(
    checkout: Path, binary: Path, build_command: list[str], revision: str
) -> dict:
    return {
        "version": "fkst.ops.invocation.v1",
        "contract": "fkst.ops.engine.v1",
        "input": {
            "engine_checkout": str(checkout),
            "engine_binary": str(binary),
            "expected_revision": revision,
            "operation": "build",
            "build_command": build_command,
        },
    }


def test_exact_revision_is_checked_out_detached_after_branch_advances(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    expected = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    advance(tmp_path / "seed", "build", "unselected branch advance")
    binary, build_command = build_script(checkout)

    result, body = invoke(payload(checkout, binary, build_command, expected))

    assert result.returncode == 0
    assert body["result"]["source_rev"] == expected
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == expected
    assert command("git", "branch", "--show-current", cwd=checkout).stdout.strip() == ""


def test_old_expected_branch_input_is_rejected(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    result, body = invoke(branch_payload(checkout, checkout / "engine", ["true"]))
    assert result.returncode == 2
    assert body["failure"]["code"] == "INVALID_INPUT"


def test_build_success(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    binary, build_command = build_script(checkout)
    revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    result, body = invoke(payload(checkout, binary, build_command, revision))
    assert result.returncode == 0
    assert body == {
        "version": "fkst.ops.invocation.v1",
        "ok": True,
        "result": {"binary": str(binary), "source_rev": command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()},
    }


def test_build_replaces_unrelated_binary_with_checkout_product(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    cargo = checkout / "cargo"
    cargo.write_text(
        "#!/bin/sh\n"
        "mkdir -p target/debug\n"
        "printf '#!/bin/sh\\n' > target/debug/engine\n"
        "chmod +x target/debug/engine\n",
        encoding="ascii",
    )
    cargo.chmod(0o755)
    binary = tmp_path / "published" / "engine"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 99\n", encoding="ascii")
    binary.chmod(0o755)

    result, body = invoke(
        payload(checkout, binary, [str(cargo), "build", "-p", "engine"], revision)
    )

    assert result.returncode == 0, body
    assert binary.is_symlink()
    assert binary.resolve() == (checkout / "target" / "debug" / "engine").resolve()


def test_exact_remote_revision_is_fetched_without_following_branch(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    binary, build_command = build_script(checkout)
    previous_revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    remote_revision = advance(tmp_path / "seed", "build", "advance build")

    result, body = invoke(payload(checkout, binary, build_command, remote_revision))

    assert result.returncode == 0
    assert body["result"]["source_rev"] == remote_revision
    assert remote_revision != previous_revision
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == remote_revision


def test_local_divergence_cannot_select_the_built_revision(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    command("git", "config", "user.email", "test@example.invalid", cwd=checkout)
    command("git", "config", "user.name", "Test", cwd=checkout)
    command("git", "commit", "--allow-empty", "-m", "local advance", cwd=checkout)
    remote_revision = advance(tmp_path / "seed", "build", "remote advance")
    binary, build_command = build_script(checkout)

    result, body = invoke(payload(checkout, binary, build_command, remote_revision))

    assert result.returncode == 0
    assert body["result"]["source_rev"] == remote_revision
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == remote_revision
    assert command("git", "branch", "--show-current", cwd=checkout).stdout.strip() == ""
    assert not (checkout / ".git" / "MERGE_HEAD").exists()


def test_missing_exact_revision_is_update_failed(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    local_revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()

    result, body = invoke(payload(checkout, checkout / "engine", ["true"], "f" * 40))

    assert result.returncode == 1
    assert body["failure"]["code"] == "UPDATE_FAILED"
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == local_revision


def test_missing_checkout_is_typed(tmp_path: Path) -> None:
    result, body = invoke(payload(tmp_path / "missing", tmp_path / "engine", ["true"], "0" * 40))
    assert result.returncode == 1
    assert body["failure"]["code"] == "CHECKOUT_MISSING"


def test_malformed_expected_revision_is_invalid_input(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    result, body = invoke(payload(checkout, checkout / "engine", ["true"], "build"))
    assert result.returncode == 2
    assert body["failure"]["code"] == "INVALID_INPUT"


def test_failed_build_is_typed(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    build = checkout / "cargo"
    build.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
    build.chmod(0o755)
    result, body = invoke(payload(
        checkout,
        checkout / "target" / "debug" / "engine",
        [str(build), "build", "-p", "engine"],
        revision,
    ))
    assert result.returncode == 1
    assert body["failure"]["code"] == "BUILD_FAILED"


def test_build_that_changes_head_fails_revision_match(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    command("git", "config", "user.email", "test@example.invalid", cwd=checkout)
    command("git", "config", "user.name", "Test", cwd=checkout)
    revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    binary = checkout / "target" / "debug" / "engine"
    build = checkout / "cargo"
    build.write_text(
        "#!/bin/sh\n"
        "git commit --allow-empty -m changed-during-build >/dev/null\n"
        "mkdir -p target/debug\n"
        "printf '#!/bin/sh\\n' > target/debug/engine\n"
        "chmod +x target/debug/engine\n",
        encoding="ascii",
    )
    build.chmod(0o755)

    result, body = invoke(payload(
        checkout, binary, [str(build), "build", "-p", "engine"], revision
    ))

    assert result.returncode == 1
    assert body["failure"]["code"] == "REVISION_MISMATCH"


def test_malformed_input_has_exact_failure_shape() -> None:
    result, body = invoke({"version": "fkst.ops.invocation.v1", "contract": "fkst.ops.engine.v1", "input": {}})
    assert result.returncode == 2
    assert body == {
        "version": "fkst.ops.invocation.v1",
        "ok": False,
        "failure": {"code": "INVALID_INPUT", "message": "input must contain exactly the engine fields", "details": {}},
    }
