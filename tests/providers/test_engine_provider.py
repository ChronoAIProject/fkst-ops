from __future__ import annotations

import json
import os
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
    binary = checkout / "out" / "engine"
    build = checkout / "build.py"
    build.write_text(
        "#!/usr/bin/env python3\nfrom pathlib import Path\np=Path('out/engine')\np.parent.mkdir()\np.write_text('#!/bin/sh\\n')\np.chmod(0o755)\n",
        encoding="ascii",
    )
    build.chmod(0o755)
    return binary, [str(build)]


def concurrent_fetch_environment(tmp_path: Path, branch: str, other_branch: str) -> dict[str, str]:
    # Intercept pull's child fetch, then let a separate fetch replace FETCH_HEAD
    # before pull selects its merge candidates.
    real_exec_path = Path(command("git", "--exec-path").stdout.strip())
    exec_path = tmp_path / "git-exec"
    exec_path.mkdir()
    for helper in real_exec_path.iterdir():
        if helper.name != "git":
            (exec_path / helper.name).symlink_to(helper)
    git = exec_path / "git"
    real_git = command("which", "git").stdout.strip()
    git.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = fetch ]; then\n"
        "  \"$REAL_GIT\" \"$@\" || exit $?\n"
        "  \"$REAL_GIT\" fetch origin \"$EXPECTED_BRANCH\" \"$OTHER_BRANCH\"\n"
        "  exit $?\n"
        "fi\n"
        "exec \"$REAL_GIT\" \"$@\"\n",
        encoding="ascii",
    )
    git.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        GIT_EXEC_PATH=str(exec_path),
        REAL_GIT=real_git,
        EXPECTED_BRANCH=branch,
        OTHER_BRANCH=other_branch,
    )
    return environment


def invoke(payload: object, env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [str(PROVIDER)], input=json.dumps(payload), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, env=env,
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
    binary, build_command = build_script(checkout)
    result, body = invoke(payload(checkout, binary, build_command))
    assert result.returncode == 0
    assert body == {
        "version": "fkst.ops.invocation.v1",
        "ok": True,
        "result": {"binary": str(binary), "source_rev": command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()},
    }


def test_update_fast_forwards_named_branch(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    binary, build_command = build_script(checkout)
    previous_revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    remote_revision = advance(tmp_path / "seed", "build", "advance build")

    result, body = invoke(payload(checkout, binary, build_command))

    assert result.returncode == 0
    assert body["result"]["source_rev"] == remote_revision
    assert remote_revision != previous_revision
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == remote_revision


def test_diverged_named_branch_is_update_failed(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    command("git", "config", "user.email", "test@example.invalid", cwd=checkout)
    command("git", "config", "user.name", "Test", cwd=checkout)
    command("git", "commit", "--allow-empty", "-m", "local advance", cwd=checkout)
    local_revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    remote_revision = advance(tmp_path / "seed", "build", "remote advance")

    result, body = invoke(payload(checkout, checkout / "engine", ["true"]))

    assert result.returncode == 1
    assert body["failure"]["code"] == "UPDATE_FAILED"
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == local_revision
    assert command("git", "rev-parse", "origin/build", cwd=checkout).stdout.strip() == remote_revision
    assert not (checkout / ".git" / "MERGE_HEAD").exists()


def test_missing_named_remote_branch_is_update_failed(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    local_revision = command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
    command("git", "config", "receive.denyDeleteCurrent", "ignore", cwd=tmp_path / "remote.git")
    command("git", "push", "origin", "--delete", "build", cwd=tmp_path / "seed")

    result, body = invoke(payload(checkout, checkout / "engine", ["true"]))

    assert result.returncode == 1
    assert body["failure"]["code"] == "UPDATE_FAILED"
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == local_revision


def test_update_ignores_other_advanced_merge_candidate(tmp_path: Path) -> None:
    checkout = repository(tmp_path)
    seed = tmp_path / "seed"
    branch = "build"
    other_branch = "other"
    command("git", "switch", "-c", other_branch, cwd=seed)
    command("git", "push", "-u", "origin", other_branch, cwd=seed)
    advance(seed, other_branch, "advance other")
    remote_revision = advance(seed, branch, "advance build")
    environment = concurrent_fetch_environment(tmp_path, branch, other_branch)

    assert command("git", "config", "--get-all", f"branch.{branch}.merge", cwd=checkout).stdout.splitlines() == [
        f"refs/heads/{branch}"
    ]
    assert command("git", "config", "--get-all", "remote.origin.fetch", cwd=checkout).stdout.splitlines() == [
        "+refs/heads/*:refs/remotes/origin/*"
    ]

    old_update = subprocess.run(
        ["git", "pull", "--ff-only"], cwd=checkout, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=environment,
    )
    merge_candidates = [
        line for line in (checkout / ".git" / "FETCH_HEAD").read_text(encoding="ascii").splitlines()
        if "not-for-merge" not in line
    ]
    assert old_update.returncode != 0
    assert "Cannot fast-forward to multiple branches" in old_update.stderr
    assert len(merge_candidates) == 2

    binary, build_command = build_script(checkout)
    result, body = invoke(payload(checkout, binary, build_command), env=environment)

    assert result.returncode == 0
    assert body["result"]["source_rev"] == remote_revision
    assert command("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip() == remote_revision


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
