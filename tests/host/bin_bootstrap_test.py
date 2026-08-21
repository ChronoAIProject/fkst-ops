import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "host" / "bin_bootstrap.sh"


def executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def test_explicit_binary_is_preserved(tmp_path):
    binary = executable(tmp_path / "engine", "#!/bin/sh\nexit 0\n")
    command = f'. "{BOOTSTRAP}"; BIN="$1"; resolve_bin_contract "$2"; printf "%s" "$RESOLVED_BIN"'
    result = subprocess.run(["bash", "-c", command, "test", str(binary), str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 0
    assert result.stdout == str(binary)


def test_explicit_binary_rejects_absent_nonexecutable_and_directory(tmp_path):
    absent = tmp_path / "absent-engine"
    nonexecutable = tmp_path / "nonexecutable-engine"
    nonexecutable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    directory = tmp_path / "engine-directory"
    directory.mkdir()
    directory.chmod(0o755)
    command = f'. "{BOOTSTRAP}"; BIN="$1"; resolve_bin_contract "$2" || printf "%s" "$RESOLVE_BIN_ERROR"'

    for candidate in (absent, nonexecutable, directory):
        result = subprocess.run(
            ["bash", "-c", command, "test", str(candidate), str(tmp_path)],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0
        assert result.stdout == f"ENGINE_BINARY_UNAVAILABLE: declared build path: {candidate}"


def test_total_miss_invokes_declared_engine_provider(tmp_path):
    remote = tmp_path / "engine.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "build", str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "test@example.invalid")
    git(seed, "config", "user.name", "Test")
    (seed / "README").write_text("engine\n", encoding="ascii")
    git(seed, "add", "README")
    git(seed, "commit", "-qm", "engine")
    revision = git(seed, "rev-parse", "HEAD")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "build")
    subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)

    platform = tmp_path / "platform"
    platform.mkdir()
    git(platform, "init", "-q")
    git(platform, "config", "user.email", "test@example.invalid")
    git(platform, "config", "user.name", "Test")
    revision_file = platform / "control" / "engine-ref"
    revision_file.parent.mkdir()
    revision_file.write_text(revision + "\n", encoding="ascii")
    git(platform, "add", ".")
    git(platform, "commit", "-qm", "platform")

    binary = tmp_path / "engine"
    build = executable(tmp_path / "cargo", '''#!/bin/sh
mkdir -p target/debug
printf '#!/bin/sh\nexit 0\n' > target/debug/engine
chmod +x target/debug/engine
''')
    env = os.environ.copy()
    env.update(CI="1", FKST_OPS_ENGINE_PROVIDER=str(ROOT / "providers/engine.py"),
               FKST_OPS_ENGINE_CHECKOUT=str(checkout), FKST_OPS_ENGINE_BINARY=str(binary),
               FKST_OPS_ENGINE_REVISION_CHECKOUT=str(platform),
               FKST_OPS_ENGINE_REVISION_PATH="control/engine-ref",
               FKST_OPS_ENGINE_CONFIGURATION=json.dumps(
                   {"build_command": [str(build), "build", "-p", "engine"]}
               ))
    command = f'. "{BOOTSTRAP}"; if ! resolve_bin_contract "$1"; then printf "%s" "$RESOLVE_BIN_ERROR" >&2; exit 1; fi; printf "%s" "$RESOLVED_BIN"'
    result = subprocess.run(["bash", "-c", command, "test", str(tmp_path)], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    revision_binary = Path(f"{binary}-{revision}")
    assert result.stdout == str(revision_binary)
    assert revision_binary.is_file()
    assert os.access(revision_binary, os.X_OK)


def test_declared_engine_provider_failure_is_not_an_empty_success(tmp_path):
    env = os.environ.copy()
    env.update(
        CI="1",
        BIN="",
        FKST_OPS_ENGINE_PROVIDER=str(ROOT / "providers/engine.py"),
        FKST_OPS_ENGINE_CHECKOUT=str(tmp_path / "missing-engine-checkout"),
        FKST_OPS_ENGINE_BINARY=str(tmp_path / "engine"),
        FKST_OPS_ENGINE_REVISION_CHECKOUT=str(tmp_path / "missing-platform-checkout"),
        FKST_OPS_ENGINE_REVISION_PATH="control/engine-ref",
        FKST_OPS_ENGINE_CONFIGURATION=json.dumps({"build_command": ["true"]}),
    )
    command = (
        f'. "{BOOTSTRAP}"; resolve_bin_contract "$1"; rc=$?; '
        'printf "%s" "$RESOLVED_BIN"; '
        'printf "resolve_rc=%s error=%s\\n" "$rc" "$RESOLVE_BIN_ERROR" >&2; '
        'exit "$rc"'
    )
    result = subprocess.run(
        ["bash", "-c", command, "test", str(tmp_path)],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "ENGINE_PROVIDER_FAILED" in result.stderr


def test_readonly_miss_does_not_invoke_provider(tmp_path):
    command = f'. "{BOOTSTRAP}"; resolve_bin_contract "$1" readonly'
    result = subprocess.run(["bash", "-c", command, "test", str(tmp_path)], text=True, capture_output=True)
    assert result.returncode != 0


def test_short_pin_uses_declared_engine_source_git_not_ambient_owner_repository():
    command = f'. "{BOOTSTRAP}"; bootstrap_parse_pin short-ref'
    environment = {
        **os.environ,
        "FKST_ENGINE_SOURCE_GIT": "https://github.com/Example-Org/engine-core.git",
        "FKST_SUBSTRATE_OWNER": "ambient-owner",
        "FKST_SUBSTRATE_REPO_NAME": "ambient-repository",
    }
    result = subprocess.run(
        ["bash", "-c", command], env=environment, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Example-Org", "engine-core", "short-ref"]


def test_short_pin_without_declared_engine_source_fails_closed():
    command = f'. "{BOOTSTRAP}"; bootstrap_parse_pin short-ref'
    environment = os.environ.copy()
    environment.pop("FKST_ENGINE_SOURCE_GIT", None)
    result = subprocess.run(
        ["bash", "-c", command], env=environment, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "short engine source pin requires FKST_ENGINE_SOURCE_GIT" in result.stderr
