from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from schema.validator import ValidationError, load_and_resolve, validate_and_resolve


FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    with (FIXTURES / name).open("rb") as handle:
        return tomllib.load(handle)


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.declaration = load("packages.toml")
        self.machine = load("machine-profile.toml")
        self.lock = load("fkst.lock")
        root = Path(self.temp.name)
        for name in self.machine["roots"]:
            directory = root / name
            directory.mkdir()
            self.machine["roots"][name] = str(directory)
        binary = root / "engine-binary"
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        binary.chmod(0o755)
        self.machine["binaries"]["engine"] = str(binary)
        self._prepare_deployment_paths(self.declaration)

    def _prepare_deployment_paths(self, declaration: dict) -> None:
        for deployment in declaration["deployment"]:
            target = Path(self.machine["roots"][deployment["machine"]["target_checkout"]])
            platform = Path(self.machine["roots"][deployment["machine"]["platform_checkout"]])
            engine = Path(self.machine["roots"][deployment["machine"]["engine_checkout"]])
            for package in deployment["packages"]["platform"]:
                (platform / "packages" / package).mkdir(parents=True, exist_ok=True)
            for package in deployment["packages"].get("host", []):
                (target / ".fkst" / "local-packages" / package).mkdir(parents=True, exist_ok=True)
            entries = {"target-source": target, "platform-source": platform, "engine-source": engine}
            for provider in declaration["provider"]:
                lock_ref, relative = provider["implementation"].split(":", 1)
                executable = entries[lock_ref] / relative
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
                executable.chmod(0o755)

    def reject(self, fragment: str) -> None:
        before = copy.deepcopy((self.declaration, self.machine, self.lock))
        with self.assertRaisesRegex(ValidationError, fragment):
            validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(before, (self.declaration, self.machine, self.lock))

    def test_all_three_topology_fixtures_resolve(self) -> None:
        for name in ("packages.toml", "substrate.toml", "website.toml"):
            with self.subTest(name=name):
                declaration = load(name)
                self._prepare_deployment_paths(declaration)
                result = validate_and_resolve(declaration, self.machine, self.lock)
                self.assertEqual(result["schema"], "fkst.ops.deployment.v1")
                self.assertTrue(result["deployment"][0]["machine"]["target_checkout"].startswith("/"))
        website_declaration = load("website.toml")
        self._prepare_deployment_paths(website_declaration)
        website = validate_and_resolve(website_declaration, self.machine, self.lock)
        self.assertEqual(website["deployment"][0]["packages"]["host"], ["site-board"])

    def test_machine_default_reference_resolves(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(result["deployment"][0]["integration"]["integration_branch"], "integration")

    def test_source_git_url_resolves_from_lock(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        source = result["deployment"][0]["sources"]["platform"]
        self.assertEqual(source["git"], "https://invalid.example/target.git")

    def test_published_mechanism_provider_binds_and_resolves(self) -> None:
        provider = self.declaration["provider"][1]
        provider["implementation"] = "fkst-ops:providers/board_engine_durable.py"
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        binding = result["deployment"][0]["providers"]["board_engine_durable"]
        self.assertEqual(Path(binding["executable"]), Path(__file__).parents[2] / "providers/board_engine_durable.py")

    def test_published_engine_provider_binds_and_command_resolves(self) -> None:
        self.declaration["provider"][0]["implementation"] = "fkst-ops:providers/engine.py"
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        deployment = result["deployment"][0]
        self.assertEqual(
            Path(deployment["providers"]["engine"]["executable"]),
            Path(__file__).parents[2] / "providers/engine.py",
        )
        self.assertEqual(deployment["providers"]["engine"]["configuration"]["build_command"], ["make", "engine"])

    def test_rejects_malformed_engine_build_command(self) -> None:
        self.declaration["provider"][0]["configuration"]["build_command"] = "make engine"
        self.reject("configuration.build_command.*non-empty string list")

    def test_rejects_missing_engine_build_command(self) -> None:
        del self.declaration["provider"][0]["configuration"]["build_command"]
        self.reject("configuration.build_command.*non-empty string list")

    def test_rejects_board_provider_configuration(self) -> None:
        self.declaration["provider"][1]["configuration"]["unexpected"] = True
        self.reject("configuration.*unknown field")

    def test_rejects_non_published_mechanism_path(self) -> None:
        self.declaration["provider"][1]["implementation"] = "fkst-ops:ops/invoke_provider.py"
        self.reject("entry point is not published for this provider kind")

    def test_rejects_absolute_provider_entry_point(self) -> None:
        self.declaration["provider"][1]["implementation"] = "fkst-ops:/providers/board_engine_durable.py"
        self.reject("entry point must be a safe relative path")

    def test_rejects_traversing_provider_entry_point(self) -> None:
        self.declaration["provider"][1]["implementation"] = "fkst-ops:providers/../providers/board_engine_durable.py"
        self.reject("entry point must be a safe relative path")

    def test_rejects_unknown_provider_kind(self) -> None:
        self.declaration["provider"][0]["kind"] = "unknown"
        self.reject("unknown provider kind")

    def test_profile_machine_references_are_optional_without_profile(self) -> None:
        del self.declaration["deployment"][0]["github_devloop_profile"]
        for field in ("rate_pool", "bot_login", "managed_bot_set"):
            del self.declaration["deployment"][0]["machine"][field]
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertNotIn("rate_pool", result["deployment"][0]["machine"])

    def test_profile_requires_its_machine_references(self) -> None:
        del self.declaration["deployment"][0]["machine"]["bot_login"]
        self.reject("machine.bot_login.*non-empty string")

    def test_rejects_unresolved_logical_reference(self) -> None:
        self.declaration["deployment"][0]["machine"]["logs"] = "missing"
        self.reject("unresolved logical roots reference")

    def test_rejects_unresolved_machine_default(self) -> None:
        self.declaration["deployment"][0]["integration"]["integration_branch"] = "machine:missing"
        self.reject("unresolved logical defaults reference")

    def test_rejects_duplicate_deployment_identity(self) -> None:
        self.declaration["deployment"].append(copy.deepcopy(self.declaration["deployment"][0]))
        self.declaration["deployment"][1]["target_identity"] = "another-target"
        self.reject("duplicate deployment identity")

    def test_rejects_duplicate_target_identity(self) -> None:
        self.declaration["deployment"].append(copy.deepcopy(self.declaration["deployment"][0]))
        self.declaration["deployment"][1]["id"] = "another-id"
        self.reject("duplicate target identity")

    def test_rejects_duplicate_provider_identity(self) -> None:
        self.declaration["provider"].append(copy.deepcopy(self.declaration["provider"][0]))
        self.reject("duplicate provider identity")

    def test_rejects_missing_source_pin(self) -> None:
        self.declaration["deployment"][0]["sources"]["target"]["lock_ref"] = "missing"
        self.reject("references missing pin")

    def test_rejects_missing_provider_implementation_pin(self) -> None:
        self.declaration["provider"][0]["implementation"] = "missing:bin/provider"
        self.reject("references missing pin")

    def test_rejects_incomplete_pin(self) -> None:
        del self.lock["external_source"][0]["resolved"]["tree_sha256"]
        self.reject("tree_sha256.*non-empty string")

    def test_rejects_unknown_declaration_field(self) -> None:
        self.declaration["surprise"] = True
        self.reject("unknown field: surprise")

    def test_rejects_unknown_nested_field(self) -> None:
        self.declaration["deployment"][0]["machine"]["surprise"] = "value"
        self.reject("unknown field: surprise")

    def test_rejects_absolute_machine_value_in_declaration(self) -> None:
        self.declaration["deployment"][0]["machine"]["target_checkout"] = "/srv/target"
        self.reject("absolute machine value is forbidden")

    def test_rejects_path_like_machine_value_in_declaration(self) -> None:
        self.declaration["deployment"][0]["machine"]["target_checkout"] = "srv/target"
        self.reject("logical name, not a path")

    def test_rejects_missing_provider_binding(self) -> None:
        del self.declaration["deployment"][0]["providers"]["engine"]
        self.reject("providers.engine.*non-empty string")

    def test_rejects_unknown_provider_binding(self) -> None:
        self.declaration["deployment"][0]["providers"]["engine"] = "missing"
        self.reject("missing provider binding")

    def test_rejects_duplicate_provider_binding(self) -> None:
        self.declaration["deployment"][0]["providers"]["board_engine_durable"] = "engine"
        self.reject("duplicate provider binding")

    def test_rejects_binding_of_wrong_kind(self) -> None:
        self.declaration["provider"][0]["kind"] = "board.engine-durable"
        self.declaration["provider"][0]["contract"] = "fkst.ops.board.engine-durable.v1"
        self.declaration["provider"][0]["configuration"] = {}
        self.reject("binding kind must be engine")

    def test_rejects_version_mismatched_provider(self) -> None:
        self.declaration["provider"][0]["contract"] = "fkst.ops.engine.v2"
        self.reject("must be fkst.ops.engine.v1")

    def test_rejects_profile_producer_binding_mismatch(self) -> None:
        self.declaration["deployment"][0]["github_devloop_profile"]["producer_binding"] = "engine-board"
        self.reject("must equal the deployment board_github_control binding")

    def test_rejects_missing_resolved_checkout(self) -> None:
        self.machine["roots"]["packages-host"] = "/definitely/missing/fkst-checkout"
        self.reject("resolved root does not exist")

    def test_rejects_missing_resolved_package_root(self) -> None:
        self.declaration["deployment"][0]["packages"]["platform"].append("missing-package")
        self.reject("resolved root does not exist")

    def test_accepts_absent_absolute_engine_build_path(self) -> None:
        binary = Path(self.temp.name) / "not-built-yet"
        self.machine["binaries"]["engine"] = str(binary)
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(result["deployment"][0]["machine"]["engine_binary"], str(binary))

    def test_accepts_non_executable_engine_build_path(self) -> None:
        binary = Path(self.temp.name) / "not-executable"
        binary.write_text("no", encoding="ascii")
        self.machine["binaries"]["engine"] = str(binary)
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(result["deployment"][0]["machine"]["engine_binary"], str(binary))

    def test_rejects_relative_engine_build_path(self) -> None:
        self.machine["binaries"]["engine"] = "relative/engine"
        self.reject("must be an absolute path")

    def test_rejects_non_executable_provider_entry(self) -> None:
        engine_root = Path(self.machine["roots"]["engine-source"])
        (engine_root / "bin" / "build-provider").chmod(0o644)
        self.reject("not executable")

    def test_rejects_duplicate_package_identity(self) -> None:
        packages = self.declaration["deployment"][0]["packages"]["platform"]
        packages.append(packages[0])
        self.reject("duplicate identity")

    def test_cli_fails_closed_without_stdout_document(self) -> None:
        broken = FIXTURES / "does-not-exist.toml"
        run = subprocess.run(
            [sys.executable, "-m", "schema.validator", str(broken), str(FIXTURES / "machine-profile.toml"), str(FIXTURES / "fkst.lock")],
            cwd=Path(__file__).parents[2], capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("deployment preflight failed", run.stderr)

    def test_cli_help_works_by_absolute_script_path_outside_checkout(self) -> None:
        run = subprocess.run(
            [sys.executable, str(Path(__file__).parents[2] / "schema" / "validator.py"), "--help"],
            cwd=self.temp.name, capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("usage:", run.stdout)


if __name__ == "__main__":
    unittest.main()
