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
    checkout = tmp_path / "checkout"; checkout.mkdir(); (checkout / ".git").mkdir()
    binary = tmp_path / "engine"
    tools = tmp_path / "tools"; tools.mkdir()
    executable(tools / "git", '''#!/bin/sh
case "$1" in branch) echo build;; pull) :;; rev-parse) printf '%040d\n' 0;; *) exit 1;; esac
''')
    build = executable(tmp_path / "build", '''#!/bin/sh
printf '#!/bin/sh\nexit 0\n' > "$1"
chmod +x "$1"
''')
    env = os.environ.copy()
    env.update(PATH=f"{tools}{os.pathsep}{env['PATH']}", FKST_OPS_ENGINE_PROVIDER=str(ROOT / "providers/engine.py"),
               FKST_OPS_ENGINE_CHECKOUT=str(checkout), FKST_OPS_ENGINE_BINARY=str(binary),
               FKST_OPS_ENGINE_BRANCH="build", FKST_OPS_ENGINE_CONFIGURATION=json.dumps({"build_command": [str(build), str(binary)]}))
    command = f'. "{BOOTSTRAP}"; resolve_bin_contract "$1"; printf "%s" "$RESOLVED_BIN"'
    result = subprocess.run(["bash", "-c", command, "test", str(tmp_path)], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(binary)
    assert binary.is_file()
    assert os.access(binary, os.X_OK)


def test_readonly_miss_does_not_invoke_provider(tmp_path):
    command = f'. "{BOOTSTRAP}"; resolve_bin_contract "$1" readonly'
    result = subprocess.run(["bash", "-c", command, "test", str(tmp_path)], text=True, capture_output=True)
    assert result.returncode != 0
