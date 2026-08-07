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


def test_total_miss_invokes_declared_engine_provider(tmp_path):
    checkout = tmp_path / "checkout"; checkout.mkdir()
    binary = tmp_path / "engine"
    log = tmp_path / "request.json"
    provider = executable(tmp_path / "provider", f'''#!/bin/sh
cat > "{log}"
printf '#!/bin/sh\\nexit 0\\n' > "{binary}"
chmod +x "{binary}"
printf '%s\\n' '{{"version":"fkst.ops.invocation.v1","ok":true,"result":{{"binary":"{binary}","source_rev":"0123456789012345678901234567890123456789"}}}}'
''')
    env = os.environ.copy()
    env.update(FKST_OPS_ENGINE_PROVIDER=str(provider), FKST_OPS_ENGINE_CHECKOUT=str(checkout),
               FKST_OPS_ENGINE_BINARY=str(binary), FKST_OPS_ENGINE_BRANCH="dev")
    command = f'. "{BOOTSTRAP}"; bootstrap_bin_on_total_miss'
    result = subprocess.run(["bash", "-c", command], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    request = json.loads(log.read_text(encoding="utf-8"))
    assert request["contract"] == "fkst.ops.engine.v1"
    assert request["input"]["engine_checkout"] == str(checkout)


def test_readonly_miss_does_not_invoke_provider(tmp_path):
    command = f'. "{BOOTSTRAP}"; resolve_bin_contract "$1" readonly'
    result = subprocess.run(["bash", "-c", command, "test", str(tmp_path)], text=True, capture_output=True)
    assert result.returncode != 0
