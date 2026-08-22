import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


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
        self.deployment.mkdir()
        (self.source / "bin").mkdir()
        (self.source / "ops").mkdir()
        (self.source / "schema").mkdir()
        shutil.copy2(ROOT / "bin" / "fkst-ops", self.source / "bin" / "fkst-ops")
        shutil.copy2(ROOT / "ops" / "public_actions.sh", self.source / "ops" / "public_actions.sh")
        runner = self.source / "ops" / "deployment_operator.sh"
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
        self.log = self.root / "calls.log"
        self.cache = self.root / "cache"
        self.write_lock(self.rev)

    def tearDown(self):
        self.temp.cleanup()

    def write_lock(self, revision: str):
        (self.deployment / "fkst.lock").write_text(
            f'[[external_source]]\nid = "fkst-ops"\ngit = "{self.source}"\n\n'
            f'[external_source.resolved]\nrev = "{revision}"\n',
            encoding="utf-8",
        )

    def invoke_action(self, action: str, *action_args: str, **overrides: str):
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log), FKST_OPS_CACHE_ROOT=str(self.cache))
        env.update(overrides)
        return run(
            "bash", str(ROOT / "bin" / "fkst-ops"), "--deployment-dir", str(self.deployment),
            "--declaration", "deployment.toml",
            "--machine-config", "machine.toml", action, *action_args,
            cwd=self.deployment, check=False, env=env,
        )

    def invoke(self, **overrides: str):
        return self.invoke_action("status", **overrides)

    def declared_actions(self) -> list[str]:
        result = run(
            "bash", "-c",
            'source "$1"; printf "%s\\n" "${FKST_OPS_PUBLIC_ACTIONS[@]}"',
            "test-public-actions", str(self.source / "ops" / "public_actions.sh"),
            cwd=self.root,
        )
        return result.stdout.splitlines()

    def commit_fixture_change(self):
        marker = self.source / "fixture-version.txt"
        marker.write_text("version two\n", encoding="utf-8")
        run("git", "add", "fixture-version.txt", cwd=self.source)
        run("git", "commit", "-qm", "fixture version two", cwd=self.source)
        return run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()

    def clone_checkout(self, destination: Path, revision: str):
        destination.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "-q", str(self.source), str(destination), cwd=self.root)
        run("git", "checkout", "-q", revision, cwd=destination)

    def install_nul_argv_recorder(self):
        runner = self.source / "ops" / "deployment_operator.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\0' \"$@\" > \"$CALL_LOG\"\n",
            encoding="utf-8",
        )
        run("git", "add", "ops/deployment_operator.sh", cwd=self.source)
        run("git", "commit", "-qm", "record exact deployment operator argv", cwd=self.source)
        revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.write_lock(revision)

    def install_pointer_validation_recorder(self):
        validator = self.source / "schema" / "validator.py"
        validator.write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "log = os.environ.get('VALIDATION_LOG')\n"
            "if log:\n"
            "    current = Path(os.environ['FKST_OPS_CACHE_ROOT']) / 'current'\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write(('published' if current.exists() else 'absent') + '\\n')\n"
            "if os.environ.get('FAKE_EXIT') not in (None, '0'): raise SystemExit(int(os.environ['FAKE_EXIT']))\n"
            "print(json.dumps({'deployment': []}))\n",
            encoding="utf-8",
        )
        run("git", "add", "schema/validator.py", cwd=self.source)
        run("git", "commit", "-qm", "record validation pointer state", cwd=self.source)
        revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.write_lock(revision)
        return revision

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

    def test_pinned_checkout_does_not_hydrate_again(self):
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log), FKST_OPS_CACHE_ROOT=str(self.cache))
        result = run(
            "bash", str(self.source / "bin" / "fkst-ops"), "--deployment-dir", str(self.deployment),
            "--declaration", "deployment.toml", "--machine-profile", "machine.toml", "status",
            cwd=self.deployment, check=False, env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.cache.exists())
        self.assertEqual(["status"], self.log.read_text(encoding="utf-8").splitlines())

    def test_pinned_validator_cannot_be_shadowed_from_working_directory(self):
        shadow = self.deployment / "schema"
        shadow.mkdir()
        marker = self.root / "shadow-validator-loaded"
        (shadow / "__init__.py").write_text("", encoding="ascii")
        (shadow / "validator.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['SHADOW_VALIDATOR_MARKER']).write_text('loaded\\n')\n",
            encoding="ascii",
        )

        result = self.invoke(SHADOW_VALIDATOR_MARKER=str(marker))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists(), "the working-directory schema.validator was imported")

    def test_modified_tracked_operator_at_pinned_revision_runs_directly(self):
        marker = self.root / "modified-operator-executed"
        runner = self.source / "ops" / "deployment_operator.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            f"touch {str(marker)!r}\n"
            "exit 73\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log), FKST_OPS_CACHE_ROOT=str(self.cache))

        result = run(
            "bash", str(self.source / "bin" / "fkst-ops"), "--deployment-dir", str(self.deployment),
            "--declaration", "deployment.toml", "--machine-profile", "machine.toml", "status",
            cwd=self.deployment, check=False, env=env,
        )

        self.assertEqual(73, result.returncode, result.stderr)
        self.assertTrue(marker.is_file())
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.log.exists())

    def test_omitted_deployment_dir_finds_nearest_lock_root(self):
        declaration = self.deployment / "deployments" / "packages.toml"
        declaration.parent.mkdir()
        declaration.write_text("", encoding="utf-8")
        cache = self.deployment / ".fkst" / "run" / "fkst-ops"
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log))

        result = run(
            "bash", str(ROOT / "bin" / "fkst-ops"), "--declaration", str(declaration),
            "--machine-config", "machine.toml", "status", cwd=self.root, check=False, env=env,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((cache / "current").is_symlink())
        self.assertFalse((declaration.parent / ".fkst").exists())

    def test_omitted_deployment_dir_without_ancestor_lock_fails_closed(self):
        outside = self.root / "outside" / "deployments" / "packages.toml"
        outside.parent.mkdir(parents=True)
        outside.write_text("", encoding="utf-8")
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log))

        result = run(
            "bash", str(ROOT / "bin" / "fkst-ops"), "--declaration", str(outside),
            "--machine-config", "machine.toml", "status", cwd=self.root, check=False, env=env,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("no deployment root containing fkst.lock found", result.stderr)
        self.assertFalse((outside.parent / ".fkst").exists())

    def test_stale_current_hydrates_and_reexecutes_new_lock_pin(self):
        self.assertEqual(0, self.invoke().returncode)
        old_target = (self.cache / "current").resolve()
        revision = self.commit_fixture_change()
        self.write_lock(revision)
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotEqual(old_target, (self.cache / "current").resolve())
        self.assertEqual(revision, (self.cache / "current").resolve().name)
        self.assertEqual(["status", "status"], self.log.read_text(encoding="utf-8").splitlines())

    def test_matching_declaration_pin_pairs_pass_and_crossed_pairs_fail_closed_after_reexec(self):
        validator = self.source / "schema" / "validator.py"
        validator.write_text(
            "import sys, tomllib\n"
            "from pathlib import Path\n"
            "deployment = tomllib.loads(Path(sys.argv[1]).read_text())['deployment'][0]\n"
            "machine = deployment['machine']\n"
            "roster = deployment['managed_bot_logins']\n"
            "if 'managed_bot_set' not in machine or len(roster) != 1: raise SystemExit(2)\n",
            encoding="ascii",
        )
        run("git", "add", "schema/validator.py", cwd=self.source)
        run("git", "commit", "-qm", "old declaration shape", cwd=self.source)
        old_revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()

        validator.write_text(
            "import sys, tomllib\n"
            "from pathlib import Path\n"
            "deployment = tomllib.loads(Path(sys.argv[1]).read_text())['deployment'][0]\n"
            "machine = deployment['machine']\n"
            "roster = deployment['managed_bot_logins']\n"
            "if 'managed_bot_set' in machine or len(roster) < 2: raise SystemExit(2)\n",
            encoding="ascii",
        )
        run("git", "add", "schema/validator.py", cwd=self.source)
        run("git", "commit", "-qm", "new declaration shape", cwd=self.source)
        new_revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()

        old_declaration = '''[[deployment]]
managed_bot_logins = ["bot-a"]
[deployment.machine]
bot_login = "github-bot"
managed_bot_set = "managed-bots"
'''
        new_declaration = '''[[deployment]]
managed_bot_logins = ["bot-a", "bot-b"]
[deployment.machine]
bot_login = "github-bot"
'''
        (self.deployment / "machine.toml").write_text("", encoding="ascii")

        # This exercises the real pin/re-exec boundary, but only proves this
        # four-cell compatibility matrix. Concurrent cross-repository delivery
        # races and downstream engine/package semantics are outside its scope.
        combinations = (
            ("old declaration with old mechanism", old_declaration, old_revision, True),
            ("new declaration with new mechanism", new_declaration, new_revision, True),
            ("new declaration with old mechanism", new_declaration, old_revision, False),
            ("old declaration with new mechanism", old_declaration, new_revision, False),
        )
        for label, declaration, revision, accepted in combinations:
            with self.subTest(label=label):
                (self.deployment / "deployment.toml").write_text(declaration, encoding="ascii")
                self.write_lock(revision)
                result = self.invoke_action("status")
                self.assertEqual(accepted, result.returncode == 0, result.stderr)

    def test_engine_revision_adoption_requires_one_declaration_and_pin_operation(self):
        validator = self.source / "schema" / "validator.py"
        validator.write_text(
            "import sys, tomllib\n"
            "from pathlib import Path\n"
            "deployment = tomllib.loads(Path(sys.argv[1]).read_text())['deployment'][0]\n"
            "if 'engine_revision' in deployment: raise SystemExit(2)\n"
            "if deployment['machine']['engine_checkout'] != 'target-checkout': raise SystemExit(2)\n",
            encoding="ascii",
        )
        run("git", "add", "schema/validator.py", cwd=self.source)
        run("git", "commit", "-qm", "old engine declaration", cwd=self.source)
        old_revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()

        validator.write_text(
            "import sys, tomllib\n"
            "from pathlib import Path\n"
            "deployment = tomllib.loads(Path(sys.argv[1]).read_text())['deployment'][0]\n"
            "if deployment.get('engine_revision') != {'path': '.control/engine-ref'}: raise SystemExit(2)\n"
            "if deployment['machine']['engine_checkout'] != 'engine-checkout': raise SystemExit(2)\n",
            encoding="ascii",
        )
        run("git", "add", "schema/validator.py", cwd=self.source)
        run("git", "commit", "-qm", "new engine declaration", cwd=self.source)
        new_revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()

        old_declaration = '''[[deployment]]
[deployment.machine]
engine_checkout = "target-checkout"
'''
        new_declaration = '''[[deployment]]
[deployment.engine_revision]
path = ".control/engine-ref"
[deployment.machine]
engine_checkout = "engine-checkout"
'''
        (self.deployment / "machine.toml").write_text("", encoding="ascii")

        sequence = (
            ("published old deployment remains operable", old_declaration, old_revision, True),
            ("atomic deployment commit selects new declaration and pin", new_declaration, new_revision, True),
            ("declaration changed before pin", new_declaration, old_revision, False),
            ("pin changed before declaration", old_declaration, new_revision, False),
        )
        for label, declaration, revision, accepted in sequence:
            with self.subTest(label=label):
                (self.deployment / "deployment.toml").write_text(declaration, encoding="ascii")
                self.write_lock(revision)
                result = self.invoke_action("status")
                self.assertEqual(accepted, result.returncode == 0, result.stderr)

    def test_hydration_reexec_preserves_action_argv(self):
        trailing = ["", "two words", "*.toml", "--option-like", "last", ""]
        self.install_nul_argv_recorder()
        env = os.environ.copy()
        env.update(CALL_LOG=str(self.log), FKST_OPS_CACHE_ROOT=str(self.cache))
        result = run(
            "bash", str(ROOT / "bin" / "fkst-ops"),
            "--declaration", "deployment.toml", "--deployment-dir", str(self.deployment),
            "--machine-config", "machine.toml", "status", *trailing,
            cwd=self.deployment, check=False, env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [value.encode() for value in ["status", *trailing]],
            self.log.read_bytes().split(b"\0")[:-1],
        )

    def test_stop_is_pinned_validated_and_delegated_but_private_primitives_are_not(self):
        stop = self.invoke_action("stop", "deployment-a", "forwarded argument")
        self.assertEqual(0, stop.returncode, stop.stderr)
        self.assertEqual(
            ["stop deployment-a forwarded argument"],
            self.log.read_text(encoding="utf-8").splitlines(),
        )
        self.assertTrue((self.cache / "current").is_symlink())

        before = self.log.read_bytes()
        for action in ("bin", "start", "config"):
            with self.subTest(action=action):
                rejected = self.invoke_action(action)
                self.assertEqual(2, rejected.returncode)
                self.assertIn("usage: fkst-ops", rejected.stderr)
                self.assertEqual(before, self.log.read_bytes())

    def test_usage_and_dispatch_are_derived_from_the_producer_owned_action_set(self):
        actions = self.source / "ops" / "public_actions.sh"
        actions.write_text(
            "#!/usr/bin/env bash\nreadonly FKST_OPS_PUBLIC_ACTIONS=(inspect quiesce)\n",
            encoding="ascii",
        )
        run("git", "add", "ops/public_actions.sh", cwd=self.source)
        run("git", "commit", "-qm", "vary public actions", cwd=self.source)
        revision = run("git", "rev-parse", "HEAD", cwd=self.source).stdout.strip()
        self.write_lock(revision)

        declared = self.declared_actions()
        self.assertEqual(["inspect", "quiesce"], declared)
        for action in declared:
            with self.subTest(action=action):
                result = self.invoke_action(action)
                self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            declared, self.log.read_text(encoding="utf-8").splitlines()
        )

        undeclared = ("shadow", "board", "status", "logs", "restart", "sync", "stop",
                      "bin", "start", "config")
        for action in undeclared:
            with self.subTest(undeclared_action=action):
                before = self.log.read_bytes()
                rejected = self.invoke_action(action, "proof")
                self.assertEqual(2, rejected.returncode, rejected.stderr)
                self.assertIn("[inspect|quiesce]", rejected.stderr)
                self.assertNotIn("board|status|logs|restart|sync|stop", rejected.stderr)
                self.assertEqual(
                    before,
                    self.log.read_bytes(),
                    f"undeclared action {action!r} reached the deployment operator",
                )

    def test_failed_stale_pin_upgrade_preserves_current_checkout_and_action_log(self):
        self.assertEqual(0, self.invoke().returncode)
        old_target = (self.cache / "current").resolve()
        old_checkouts = sorted(path.name for path in (self.cache / "checkouts").iterdir())
        old_log = self.log.read_bytes()
        revision = self.commit_fixture_change()
        self.write_lock(revision)

        result = self.invoke(FAKE_EXIT="1")

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(old_target, (self.cache / "current").resolve())
        self.assertEqual(old_checkouts, sorted(path.name for path in (self.cache / "checkouts").iterdir()))
        self.assertEqual(old_log, self.log.read_bytes())

    def test_existing_verified_checkout_is_preflighted_published_and_delegated(self):
        revision = self.install_pointer_validation_recorder()
        verified = self.cache / "checkouts" / revision
        self.clone_checkout(verified, revision)
        self.assertFalse((self.cache / "current").exists())
        validation_log = self.root / "validations.log"

        result = self.invoke(VALIDATION_LOG=str(validation_log))

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(verified.resolve(), (self.cache / "current").resolve())
        self.assertEqual(
            ["absent", "published"],
            validation_log.read_text(encoding="utf-8").splitlines(),
        )
        self.assertEqual(["status"], self.log.read_text(encoding="utf-8").splitlines())

    def test_existing_requested_revision_checkout_with_wrong_head_fails_closed(self):
        old_revision = self.rev
        revision = self.commit_fixture_change()
        self.write_lock(revision)
        self.clone_checkout(self.cache / "checkouts" / revision, old_revision)

        result = self.invoke()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("resolved.rev mismatch", result.stderr)
        self.assertFalse((self.cache / "current").exists())
        self.assertFalse(self.log.exists())

    def test_wrong_requested_revision_fails_closed(self):
        self.write_lock("f" * 40)
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.cache / "current").exists())
        self.assertFalse(self.log.exists())

    def test_poisoned_preexisting_checkout_fails_closed(self):
        poisoned = self.cache / "checkouts" / self.rev
        poisoned.mkdir(parents=True)
        (poisoned / ".git").mkdir()
        result = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("resolved.rev mismatch", result.stderr)
        self.assertFalse((self.cache / "current").exists())
        self.assertFalse(self.log.exists())

    def test_reexec_loop_is_explicitly_bounded(self):
        result = self.invoke(FKST_OPS_REEXEC_DEPTH="1")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("re-exec target is not the physically pinned checkout", result.stderr)
        self.assertFalse(self.cache.exists())

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
