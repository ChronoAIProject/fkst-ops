import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"
ENTRY = ROOT / "host" / "supervise.sh"


def test_operator_constructs_the_contract_argv_with_the_owned_entry() -> None:
    source = OPERATOR.read_text(encoding="utf-8")
    launch = source[source.index("launch_one() {") : source.index("launch_with_lock_retry() {")]
    expected = '''args=(
    "$_repo_root/host/supervise.sh"
    --project-root "$HOST"
    --platform-root "$launch_platform"
    --platform-packages "$PLATFORM_PKGS"
    --expected-engine-revision "$ENGINE_REVISION"
    --durable-root "$DUR"
    --runtime-root "$rt"
  )'''

    assert expected in launch
    assert "$launch_platform/scripts/run.sh" not in launch
    assert '"$_self_dir/launch_child.py" --spawn' in launch
    assert 'wait_supervise_ready "$pid" "$log"' in launch
    assert 'svpgid=$(ps -o pgid= -p "$pid"' in launch


def test_owned_entry_is_executable_and_dispatches_to_the_contract() -> None:
    assert os.access(ENTRY, os.X_OK)
    result = subprocess.run([str(ENTRY), "--help"], text=True, capture_output=True)

    assert result.returncode == 2
    assert "usage: scripts/run.sh supervise --project-root" in result.stderr


def test_resolved_engine_git_is_carried_into_the_host_environment() -> None:
    operator = OPERATOR.read_text(encoding="utf-8")
    environment = (ROOT / "ops" / "deployment_launch_environment.sh").read_text(
        encoding="utf-8"
    )

    assert 'dep["sources"]["engine"]["git"]' in operator
    assert 'ENGINE_GIT_URL' in operator
    assert 'FKST_ENGINE_SOURCE_GIT="$ENGINE_GIT_URL"' in environment
