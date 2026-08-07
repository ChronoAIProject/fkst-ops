import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "deployments" / "packages"


class PackagesBundleTest(unittest.TestCase):
    def validate(self, declaration: Path, profile: Path, lock: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "schema.validator", str(declaration), str(profile), str(lock)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def materialize_profile(self, root: Path) -> Path:
        target = root / "target"
        engine = root / "engine"
        durable = root / "durable"
        runtime = root / "runtime"
        logs = root / "logs"
        rate_pool = root / "rate-pool"
        for path in (target, engine, durable, runtime, logs, rate_pool):
            path.mkdir()
        for package in self.platform_packages():
            (target / "packages" / package).mkdir(parents=True)
        for checkout in (target, engine):
            script = checkout / "scripts" / "run.sh"
            script.parent.mkdir(exist_ok=True)
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
        binary = engine / "fkst-framework"
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
        profile = root / "machine-profile.toml"
        profile.write_text(
            "\n".join(
                [
                    'schema = "fkst.ops.machine-profile.v1"',
                    "[roots]",
                    f'packages-target-checkout = "{target}"',
                    f'packages-platform-checkout = "{target}"',
                    f'packages-engine-checkout = "{engine}"',
                    f'packages-durable = "{durable}"',
                    f'packages-runtime = "{runtime}"',
                    f'packages-logs = "{logs}"',
                    f'packages-rate-pool = "{rate_pool}"',
                    "[binaries]",
                    f'packages-engine-binary = "{binary}"',
                    "[credentials]",
                    'packages-bot-login = "fixture-bot"',
                    "[sets]",
                    'packages-managed-bots = ["fixture-bot", "fixture-peer"]',
                    "[defaults]",
                    'packages-integration-branch = "integration"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return profile

    def platform_packages(self) -> list[str]:
        with (BUNDLE / "deployment.toml").open("rb") as handle:
            return tomllib.load(handle)["deployment"][0]["packages"]["platform"]

    def test_real_validator_accepts_bundle_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self.materialize_profile(Path(temp))
            result = self.validate(BUNDLE / "deployment.toml", profile, BUNDLE / "fkst.lock")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_real_validator_rejects_broken_logical_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            profile = self.materialize_profile(temp_root)
            declaration = temp_root / "broken.toml"
            declaration.write_text(
                (BUNDLE / "deployment.toml").read_text(encoding="utf-8").replace(
                    'durable = "packages-durable"', 'durable = "missing-durable"', 1
                ),
                encoding="utf-8",
            )
            result = self.validate(declaration, profile, BUNDLE / "fkst.lock")
        self.assertEqual(2, result.returncode)
        self.assertIn("unresolved logical roots reference: missing-durable", result.stderr)


if __name__ == "__main__":
    unittest.main()
