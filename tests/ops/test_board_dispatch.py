import os
import shutil
import subprocess
from pathlib import Path

from ops.revision_derivation import write_build_receipt


ROOT = Path(__file__).resolve().parents[2]


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_board_dispatch_contract_carries_validator_actor_and_complete_roster(
    tmp_path, fabricated_mechanism_tools
):
    mechanism = tmp_path / "mechanism"
    shutil.copytree(
        ROOT,
        mechanism,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
    )
    subprocess.run(["git", "init", "-q", str(mechanism)], check=True)
    subprocess.run(
        ["git", "-C", str(mechanism), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(mechanism), "config", "user.name", "test"], check=True,
    )
    subprocess.run(["git", "-C", str(mechanism), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(mechanism), "commit", "-qm", "fixture mechanism"],
        check=True,
    )
    source = tmp_path / "source"; engine = tmp_path / "engine"
    durable = tmp_path / "durable"; runtime = tmp_path / "runtime"; logs = tmp_path / "logs"
    for path in (source, engine, durable, runtime, logs): path.mkdir()
    (source / "packages" / "workflow").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "init", "-q", str(engine)], check=True)
    binary_stem = engine / "engine-bin"
    provider = '''#!/usr/bin/env python3
import json,os,sys
if os.environ.get("PROVIDER_CALL_MARKER"):
    open(os.environ["PROVIDER_CALL_MARKER"], "a").close()
r=json.load(sys.stdin); view="engine-durable" if "engine-durable" in r["contract"] else "github-control"
if view=="github-control":
    assert r["input"]["bot_login"] == "Local-Bot[bot]"
    assert r["input"]["managed_bot_set"] == ["Local-Bot", "peer-bot[bot]"]
result={"view":view,"rows":[{"key":view,"classification":"fixture","fields":{"text":view+" through operator"}}]}
if view=="engine-durable": result["health"]={"status":"healthy","anomalies":[]}
print(json.dumps({"version":"fkst.ops.invocation.v1","ok":True,"result":result}))
'''
    executable(source / "providers" / "board", provider)
    executable(engine / "providers" / "build", provider)
    for repository in (source, engine):
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(engine), "add", "."], check=True)
    subprocess.run(["git", "-C", str(engine), "commit", "-qm", "engine"], check=True)
    engine_revision = subprocess.run(
        ["git", "-C", str(engine), "rev-parse", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    (source / "control").mkdir()
    (source / "control" / "engine-ref").write_text(engine_revision + "\n", encoding="ascii")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "platform"], check=True)
    binary = Path(f"{binary_stem}-{engine_revision}")
    executable(binary, "#!/bin/sh\nexit 0\n")
    write_build_receipt(binary, engine_revision, [shutil.which("true")])
    declaration = tmp_path / "deployment.toml"
    declaration.write_text('''schema="fkst.ops.deployment.v1"
cadence_enabled=true
cadence_interval_seconds=300
guard_restart_attempt_limit=3
[[deployment]]
id="fixture"
target_identity="owner/target"
managed_bot_logins=["Local-Bot", "peer-bot[bot]"]
[deployment.claim_posture]
mode="assignee"
label_exclusive=false
[deployment.sources.target]
lock_ref="source"
[deployment.sources.platform]
lock_ref="source"
[deployment.sources.engine]
lock_ref="engine"
[deployment.engine_revision]
path="control/engine-ref"
[deployment.packages]
platform=["workflow"]
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
bot_login="github-bot"
[deployment.providers]
github_credential="github-credential"
engine="engine"
board_engine_durable="engine-board"
board_github_control="github-board"
[[provider]]
id="github-credential"
kind="credential.github"
implementation="fkst-ops:providers/github_credential_gh.py"
contract="fkst.ops.credential.github.v1"
configuration={source="github-app"}
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
binary="{binary_stem}"
[tools]
true="{shutil.which('true')}"
codex="{shutil.which('codex')}"
gh="{shutil.which('true')}"
gh-app="{shutil.which('true')}"
[credentials]
github-bot="Local-Bot[bot]"
[sets]
[defaults]
''', encoding="utf-8")
    lock = tmp_path / "fkst.lock"
    mechanism_rev = subprocess.run(
        ["git", "-C", str(mechanism), "rev-parse", "HEAD"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    lock.write_text(f'''[[external_source]]
id="fkst-ops"
git="{mechanism}"
checkout_role="mechanism"
[external_source.resolved]
rev="{mechanism_rev}"
[[external_source]]
id="source"
git="https://invalid.example/source.git"
checkout_role="deployment-operated"
[[external_source]]
id="engine"
git="https://invalid.example/engine.git"
checkout_role="deployment-operated"
''', encoding="utf-8")
    result = subprocess.run([str(mechanism / "bin" / "fkst-ops"), "--declaration", str(declaration),
                             "--machine-config", str(profile), "--lock", str(lock), "board", "fixture"],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "MISSING github-control: plane is not implemented" in result.stdout
    assert "github-control through operator" not in result.stdout
    assert "engine-durable through operator" in result.stdout

    binary.unlink()
    marker = tmp_path / "provider-called"
    env = os.environ.copy()
    env["PROVIDER_CALL_MARKER"] = str(marker)
    unavailable = subprocess.run(
        [str(mechanism / "bin" / "fkst-ops"), "--declaration", str(declaration),
         "--machine-config", str(profile), "--lock", str(lock), "board", "fixture"],
        cwd=tmp_path, text=True, capture_output=True, env=env,
    )
    assert unavailable.returncode != 0
    assert "ENGINE_BUILD_RECEIPT_MISMATCH" in unavailable.stderr
    assert str(binary) in unavailable.stderr
    assert "deployment preflight failed" not in unavailable.stderr
    assert not marker.exists()
