#!/usr/bin/env python3
"""N+1 deployments require no fkst-ops source changes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ("board", "status", "logs", "restart", "sync")


def byte_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        if path.is_symlink():
            data = os.readlink(path).encode()
            kind = b"L"
        elif path.is_file():
            data = path.read_bytes()
            kind = b"F"
        else:
            data = b""
            kind = b"D"
        digest.update(kind + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


class NPlusOneAcceptanceTest(unittest.TestCase):
    def test_one_declaration_and_pin_leave_fkst_ops_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            mechanism = temporary / "fkst-ops"
            deployment = temporary / "deployment-n-plus-one"
            shutil.copytree(ROOT, mechanism, symlinks=True)
            deployment.mkdir()
            target = deployment / "target"
            engine = deployment / "engine"
            runtime = deployment / "runtime"
            durable = deployment / "durable"
            logs = deployment / "logs"
            for path in (target, engine, runtime, durable, logs):
                path.mkdir()
            (logs / "n-plus-one-sv-1.log").write_text("older\n", encoding="ascii")
            (logs / "n-plus-one-sv-2.log").write_text("newer\n", encoding="ascii")
            (engine / "fkst-framework").write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
            (engine / "fkst-framework").chmod(0o755)
            for package in ("github-devloop", "github-devloop-pr", "github-devloop-integration"):
                (target / "packages" / package).mkdir(parents=True)
            providers = (
                (engine, "bin/build-provider"),
                (target, "providers/engine-board"),
                (target, "providers/github-board"),
            )
            for source, relative in providers:
                provider = source / relative
                provider.parent.mkdir(parents=True, exist_ok=True)
                provider.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
                provider.chmod(0o755)
            for repository in (target, engine):
                subprocess.run(["git", "init", "-q", str(repository)], check=True)
                subprocess.run(["git", "-C", str(repository), "config", "user.email", "n+1@example.invalid"], check=True)
                subprocess.run(["git", "-C", str(repository), "config", "user.name", "N Plus One"], check=True)
                subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
                subprocess.run(["git", "-C", str(repository), "commit", "-qm", "fixture"], check=True)

            declaration = deployment / "deployment.toml"
            machine = deployment / "machine.toml"
            lock = deployment / "fkst.lock"
            declaration.write_text((ROOT / "tests/schema/fixtures/packages.toml").read_text().replace('id = "packages"', 'id = "n-plus-one"', 1), encoding="utf-8")
            machine.write_text(f'''schema = "fkst.ops.machine-profile.v1"
[roots]
packages-host = "{target}"
engine-source = "{engine}"
packages-durable = "{durable}"
packages-runtime = "{runtime}"
packages-logs = "{logs}"
shared-rate-pool = "{deployment / 'rates'}"
[binaries]
engine = "{engine / 'fkst-framework'}"
[credentials]
github-bot = "fixture-bot"
[sets]
    managed-bots = ["fkst-bot"]
[defaults]
integration-branch = "integration"
''', encoding="utf-8")
            lock.write_text(f'''[[external_source]]
id = "fkst-ops"
git = "{mechanism}"
[external_source.resolved]
rev = "0000000000000000000000000000000000000000"
tree_sha256 = "sha256-{'0' * 64}"
[[external_source]]
id = "target-source"
git = "{target}"
[external_source.resolved]
rev = "1111111111111111111111111111111111111111"
tree_sha256 = "sha256-{'1' * 64}"
[[external_source]]
id = "engine-source"
git = "{engine}"
[external_source.resolved]
rev = "2222222222222222222222222222222222222222"
tree_sha256 = "sha256-{'2' * 64}"
''', encoding="utf-8")

            before = byte_tree(mechanism)
            env = os.environ.copy()
            env.update({
                "FKST_OPS_DECLARATION": str(declaration),
                "FKST_OPS_MACHINE_PROFILE": str(machine),
                "FKST_OPS_LOCK": str(lock),
            })
            results = {}
            for action in ACTIONS:
                results[action] = subprocess.run(
                    [str(mechanism / "ops/deployment_operator.sh"), action, "n-plus-one"],
                    cwd=deployment, env=env, text=True, capture_output=True,
                    timeout=8, check=False,
                ).returncode
            self.assertEqual(set(results), set(ACTIONS))
            self.assertEqual(results["status"], 0, results)
            self.assertEqual(results["logs"], 0, results)
            self.assertEqual(before, byte_tree(mechanism), results)


if __name__ == "__main__":
    unittest.main()
