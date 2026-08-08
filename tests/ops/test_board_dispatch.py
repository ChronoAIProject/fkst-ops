import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_dispatcher_runs_operator_board_action(tmp_path):
    source = tmp_path / "source"; engine = tmp_path / "engine"
    durable = tmp_path / "durable"; runtime = tmp_path / "runtime"; logs = tmp_path / "logs"
    for path in (source, engine, durable, runtime, logs): path.mkdir()
    (source / "packages" / "workflow").mkdir(parents=True)
    (source / "fkst.workspace.toml").write_text('[[external_sources]]\nid="source"\npackages=["workflow"]\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", str(engine)], check=True)
    binary = engine / "engine-bin"; executable(binary, "#!/bin/sh\nexit 0\n")
    provider = '''#!/usr/bin/env python3
import json,os,sys
if os.environ.get("PROVIDER_CALL_MARKER"):
    open(os.environ["PROVIDER_CALL_MARKER"], "a").close()
r=json.load(sys.stdin); view="engine-durable" if "engine-durable" in r["contract"] else "github-control"
result={"view":view,"rows":[{"key":view,"classification":"fixture","fields":{"text":view+" through operator"}}]}
if view=="engine-durable": result["health"]={"status":"healthy","anomalies":[]}
print(json.dumps({"version":"fkst.ops.invocation.v1","ok":True,"result":result}))
'''
    executable(source / "providers" / "board", provider)
    executable(engine / "providers" / "build", provider)
    declaration = tmp_path / "deployment.toml"
    declaration.write_text('''schema="fkst.ops.deployment.v1"
[[deployment]]
id="fixture"
target_identity="owner/target"
[deployment.sources.target]
lock_ref="source"
[deployment.sources.platform]
lock_ref="source"
[deployment.sources.engine]
lock_ref="engine"
[deployment.packages]
platform=["workflow"]
host=[]
[deployment.integration]
upstream_branch="dev"
integration_branch="dev"
rollup_merge="manual"
[deployment.machine]
target_checkout="source"
platform_checkout="source"
engine_checkout="engine"
engine_binary="binary"
durable="durable"
runtime="runtime"
logs="logs"
[deployment.providers]
engine="engine"
board_engine_durable="engine-board"
board_github_control="github-board"
[[provider]]
id="engine"
kind="engine"
implementation="engine:providers/build"
contract="fkst.ops.engine.v1"
configuration={build_command=["true"]}
[[provider]]
id="engine-board"
kind="board.engine-durable"
implementation="source:providers/board"
contract="fkst.ops.board.engine-durable.v1"
configuration={}
[[provider]]
id="github-board"
kind="board.github-control"
implementation="source:providers/board"
contract="fkst.ops.board.github-control.v1"
configuration={}
''', encoding="utf-8")
    profile = tmp_path / "machine.toml"
    profile.write_text(f'''schema="fkst.ops.machine-profile.v1"
[roots]
source="{source}"
engine="{engine}"
durable="{durable}"
runtime="{runtime}"
logs="{logs}"
[binaries]
binary="{binary}"
[credentials]
[sets]
[defaults]
''', encoding="utf-8")
    lock = tmp_path / "fkst.lock"
    pin = '0' * 40; tree = 'sha256-' + '0' * 64
    mechanism_rev = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    mechanism_tree = subprocess.run(
        ["python3", str(ROOT / "bootstrap" / "canonical_tree.py"), str(ROOT), mechanism_rev],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    lock.write_text(f'''[[external_source]]
id="fkst-ops"
git="{ROOT}"
[external_source.resolved]
rev="{mechanism_rev}"
tree_sha256="{mechanism_tree}"
[[external_source]]
id="source"
git="https://invalid.example/source.git"
[external_source.resolved]
rev="{pin}"
tree_sha256="{tree}"
[[external_source]]
id="engine"
git="https://invalid.example/engine.git"
[external_source.resolved]
rev="{pin}"
tree_sha256="{tree}"
''', encoding="utf-8")
    result = subprocess.run([str(ROOT / "bin" / "fkst-ops"), "--declaration", str(declaration),
                             "--machine-config", str(profile), "--lock", str(lock), "board", "fixture"],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "github-control through operator" in result.stdout
    assert "engine-durable through operator" in result.stdout

    binary.unlink()
    marker = tmp_path / "provider-called"
    env = os.environ.copy()
    env["PROVIDER_CALL_MARKER"] = str(marker)
    unavailable = subprocess.run(
        [str(ROOT / "bin" / "fkst-ops"), "--declaration", str(declaration),
         "--machine-config", str(profile), "--lock", str(lock), "board", "fixture"],
        text=True, capture_output=True, env=env,
    )
    assert unavailable.returncode != 0
    assert "ENGINE_BINARY_UNAVAILABLE" in unavailable.stderr
    assert f"declared build path: {binary}" in unavailable.stderr
    assert "deployment preflight failed" not in unavailable.stderr
    assert not marker.exists()
