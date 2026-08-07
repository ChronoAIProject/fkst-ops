import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "bootstrap"
ROOT = Path(__file__).resolve().parents[2]


def run(*args: str, cwd: Path, check: bool = True, env: dict[str, str] | None = None):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check, env=env)


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.deployment = self.root / "deployment"
        self.source.mkdir()
        (self.source / "bin").mkdir()
        (self.source / "ops").mkdir()
        (self.source / "schema").mkdir()
        shutil.copytree(SOURCE, self.deployment / "bootstrap")
        shutil.copy2(ROOT / "bin" / "fkst-ops", self.source / "bin" / "fkst-ops")
        runner = self.source / "ops" / "dogfood.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "exit \"${FAKE_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
        (self.source / "schema" / "__init__.py").write_text("", encoding="utf-8")
        (self.source / "schema" / "validator.py").write_text(
            "import json, os\n"
            "if os.environ.get('FAKE_EXIT') not in (None, '0'): raise SystemExit(int(os.environ['FAKE_EXIT']))\n"
            "print(json.dumps({'deployment': []}))\n", encoding="utf-8"
        )
        run("git", "init", "-q", cwd=self.source)
        run("git", "config", "user.email", "test@example.invalid", cwd=self.source)
        run("git", "config", "user.name", "Test", cwd=self.source)
        run("git", "add", ".", cwd=self.source)
        run("git", "commit", "-qm", "fixture", cwd=self.source)
        self.rev = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.tree = run(
            "python3", str(SOURCE / "canonical_tree.py"), str(self.source), self.rev, cwd=self.root
        ).stdout.strip()
        self.log = self.root / "calls.log"
        self.cache = self.root / "cache"
        self.write_lock(self.rev, self.tree)

    def tearDown(self):
        self.temp.cleanup()

    def write_lock(self, revision: str, tree: str):
        (self.deployment / "fkst.lock").write_text(
            f'[[external_source]]\nid = "fkst-ops"\ngit = "{self.source}"\n\n'
            f'[external_source.resolved]\nrev = "{revision}"\ntree_sha256 = "{tree}"\n',
            encoding="utf-8",
        )

    def invoke(self, **overrides: str):
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log), FKST_OPS_CACHE_ROOT=str(self.cache))
        env.update(overrides)
        return run(
            "bash", str(self.deployment / "bootstrap" / "run.sh"), "deployment.toml",
            "--machine-config", "machine.toml", "status", cwd=self.deployment, check=False, env=env,
        )

    def test_fresh_then_cached_checkout_is_verified_and_delegated(self):
        first = self.invoke()
        self.assertEqual(0, first.returncode, first.stderr)
        pointer = self.cache / "current"
        self.assertTrue(pointer.is_symlink())
        first_target = pointer.resolve()
        second = self.invoke()
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(first_target, pointer.resolve())
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(calls))
        self.assertTrue(all(call == "status" for call in calls))

    def test_cached_resolved_revision_mismatch_fails_before_delegation(self):
        self.assertEqual(0, self.invoke().returncode)
        run("git", "commit", "--allow-empty", "-qm", "other", cwd=self.source)
        other = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.write_lock(other, self.tree)
        before = self.log.read_bytes()
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("resolved.rev mismatch", result.stderr)
        self.assertEqual(before, self.log.read_bytes())

    def test_tree_hash_mismatch_fails_without_cache_pointer(self):
        self.write_lock(self.rev, "sha256-" + hashlib.sha256(b"wrong").hexdigest())
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("tree_sha256 mismatch", result.stderr)
        self.assertFalse((self.cache / "current").exists())
        self.assertFalse(self.log.exists())

    def test_cached_tree_hash_is_verified_before_delegation(self):
        self.assertEqual(0, self.invoke().returncode)
        target = (self.cache / "current").resolve()
        self.write_lock(self.rev, "sha256-" + hashlib.sha256(b"wrong").hexdigest())
        before = self.log.read_bytes()
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("tree_sha256 mismatch", result.stderr)
        self.assertEqual(target, (self.cache / "current").resolve())
        self.assertEqual(before, self.log.read_bytes())

    def test_acquisition_failure_cleans_partial_state(self):
        missing = self.root / "missing-source"
        lock = (self.deployment / "fkst.lock").read_text(encoding="utf-8")
        (self.deployment / "fkst.lock").write_text(
            lock.replace(str(self.source), str(missing)), encoding="utf-8"
        )
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.cache / "current").exists())
        self.assertEqual([], list((self.cache / "checkouts").glob(".partial.*")))

    def test_failed_preflight_does_not_promote_candidate(self):
        result = self.invoke(FAKE_EXIT="1")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.cache / "current").exists())
        self.assertEqual([], list((self.cache / "checkouts").glob(".partial.*")))


if __name__ == "__main__":
    unittest.main()
