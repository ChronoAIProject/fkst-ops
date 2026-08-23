"""A declaration may name package sources beyond its platform.

A deployment whose target holds no Lua of its own still has to get its packages from
somewhere. `sources.platform` names one source; these entries name the rest, each binding a
pinned repository to a machine checkout and listing what it supplies. That is what lets the
composition live where the deployment is declared rather than inside the repository being
operated, so the operated repository need not describe how it is composed.

Every rejection below is a way two roots or two names could collapse into one. The engine
addresses a loaded package by bare name and the launch contract carries roots as paths, so a
collision here is not a validation nicety: it is a package silently resolving to the wrong
source.
"""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import tomllib
import unittest

from schema.validator import ValidationError, validate_and_resolve


FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    with (FIXTURES / name).open("rb") as handle:
        return tomllib.load(handle)


class PackageSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.declaration = load("website.toml")
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
        tool = root / "make"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o755)
        self.machine["tools"] = {"make": str(tool)}
        self._materialise()

    def _materialise(self) -> None:
        deployment = self.declaration["deployment"][0]
        machine = deployment["machine"]
        platform = Path(self.machine["roots"][machine["platform_checkout"]])
        engine = Path(self.machine["roots"][machine["engine_checkout"]])
        for package in deployment["packages"]["platform"]:
            (platform / "packages" / package).mkdir(parents=True, exist_ok=True)
        for entry in deployment.get("package_sources", []):
            source_root = Path(self.machine["roots"][entry["checkout"]])
            for package in entry["packages"]:
                (source_root / "packages" / package).mkdir(parents=True, exist_ok=True)
        target = Path(self.machine["roots"][machine["target_checkout"]])
        entries = {"target-source": target, "platform-source": platform, "engine-source": engine}
        for provider in self.declaration["provider"]:
            lock_ref, relative = provider["implementation"].split(":", 1)
            if lock_ref == "fkst-ops":
                continue
            executable = entries[lock_ref] / relative
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            executable.chmod(0o755)

    @property
    def entry(self) -> dict:
        return self.declaration["deployment"][0]["package_sources"][0]

    def resolve(self) -> dict:
        return validate_and_resolve(self.declaration, self.machine, self.lock)

    def reject(self, fragment: str) -> None:
        before = copy.deepcopy((self.declaration, self.machine, self.lock))
        with self.assertRaisesRegex(ValidationError, fragment):
            validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(before, (self.declaration, self.machine, self.lock))

    # The capability itself.

    def test_a_named_package_source_resolves_to_its_own_root(self) -> None:
        resolved = self.resolve()["deployment"][0]["package_sources"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["lock_ref"], "package-source")
        self.assertEqual(resolved[0]["packages"], ["site-board"])
        self.assertEqual(resolved[0]["git"], "https://invalid.example/packages-extra.git")
        self.assertEqual(
            resolved[0]["checkout"], self.machine["roots"]["website-extra-packages"]
        )
        # The logical name survives resolution: hydration and staleness address the checkout by
        # the name the machine profile knows, not by the path.
        self.assertEqual(resolved[0]["checkout_reference"], "website-extra-packages")

    def test_a_declaration_without_package_sources_resolves_to_an_empty_list(self) -> None:
        del self.declaration["deployment"][0]["package_sources"]
        self.assertEqual(self.resolve()["deployment"][0]["package_sources"], [])

    def test_two_package_sources_each_keep_their_own_root(self) -> None:
        self.lock["external_source"].append({
            "id": "second-package-source",
            "git": "https://invalid.example/second.git",
            "checkout_role": "deployment-operated",
        })
        second = Path(self.temp.name) / "second-extra"
        second.mkdir()
        self.machine["roots"]["website-second-packages"] = str(second)
        self.declaration["deployment"][0]["package_sources"].append({
            "lock_ref": "second-package-source",
            "checkout": "website-second-packages",
            "packages": ["site-radar"],
        })
        self._materialise()
        resolved = self.resolve()["deployment"][0]["package_sources"]
        self.assertEqual(
            [(entry["lock_ref"], entry["checkout"]) for entry in resolved],
            [("package-source", self.machine["roots"]["website-extra-packages"]),
             ("second-package-source", str(second))],
        )

    def test_a_package_source_cannot_supply_a_provider_implementation(self) -> None:
        provider = self.declaration["provider"][0]
        provider["implementation"] = "package-source:bin/build-provider"
        executable = Path(self.machine["roots"][self.entry["checkout"]]) / "bin/build-provider"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        executable.chmod(0o755)

        self.reject(
            r"declaration\.deployment\[0\]\.providers\.engine: "
            r"provider source is not bound to a deployment or mechanism source: package-source"
        )

    def test_target_platform_engine_and_mechanism_sources_can_supply_providers(self) -> None:
        self.declaration["provider"][1]["implementation"] = (
            "target-source:providers/engine-board"
        )
        self._materialise()

        bindings = self.resolve()["deployment"][0]["providers"]
        deployment = self.declaration["deployment"][0]
        machine = deployment["machine"]
        expected = {
            "engine": Path(self.machine["roots"][machine["engine_checkout"]]).resolve()
            / "bin/build-provider",
            "board_engine_durable": Path(
                self.machine["roots"][machine["target_checkout"]]
            ).resolve()
            / "providers/engine-board",
            "board_github_control": Path(
                self.machine["roots"][machine["platform_checkout"]]
            ).resolve()
            / "providers/github-board",
            "github_credential": Path(__file__).parents[2].resolve()
            / "providers/github_credential_gh.py",
        }
        self.assertEqual(
            {field: Path(provider["executable"]) for field, provider in bindings.items()},
            expected,
        )

    # Ways two sources or two names could collapse into one.

    def test_an_unknown_field_is_rejected(self) -> None:
        self.entry["rev"] = "0" * 40
        self.reject(r"package_sources\[0\].*unknown field: rev")

    def test_a_missing_pin_is_rejected(self) -> None:
        self.entry["lock_ref"] = "absent"
        self.reject(r"package_sources\[0\].lock_ref.*missing pin: absent")

    def test_a_mechanism_pin_is_rejected(self) -> None:
        self.entry["lock_ref"] = "fkst-ops"
        self.reject(r"package_sources\[0\].lock_ref.*deployment-operated")

    def test_a_pin_already_used_as_a_deployment_source_is_rejected(self) -> None:
        # Otherwise one repository would be checked out twice under two roots, and a package
        # could resolve against whichever copy the launch contract ordered first.
        self.entry["lock_ref"] = "platform-source"
        self.reject(r"package_sources\[0\].lock_ref.*already declared as a deployment source")

    def test_a_repeated_package_source_is_rejected(self) -> None:
        self.declaration["deployment"][0]["package_sources"].append(copy.deepcopy(self.entry))
        self.reject(r"package_sources\[1\].lock_ref.*duplicate package source")

    def test_an_unresolved_checkout_reference_is_rejected(self) -> None:
        self.entry["checkout"] = "no-such-root"
        self.reject(r"package_sources\[0\].checkout.*unresolved logical roots reference")

    def test_a_checkout_shared_with_an_operated_role_is_rejected(self) -> None:
        # The target, platform and engine checkouts are branch- or revision-operated: hydration
        # resets them. A package source sharing one would have its contents replaced underneath.
        for role in ("target", "platform", "engine"):
            with self.subTest(role=role):
                declaration = copy.deepcopy(self.declaration)
                machine = declaration["deployment"][0]["machine"]
                declaration["deployment"][0]["package_sources"][0]["checkout"] = machine[f"{role}_checkout"]
                with self.assertRaisesRegex(
                    ValidationError, rf"package_sources\[0\].checkout.*separate from the {role} checkout"
                ):
                    validate_and_resolve(declaration, self.machine, self.lock)

    def test_two_package_sources_sharing_one_checkout_are_rejected(self) -> None:
        self.lock["external_source"].append({
            "id": "second-package-source",
            "git": "https://invalid.example/second.git",
            "checkout_role": "deployment-operated",
        })
        self.declaration["deployment"][0]["package_sources"].append({
            "lock_ref": "second-package-source",
            "checkout": self.entry["checkout"],
            "packages": ["site-radar"],
        })
        self._materialise()
        self.reject(r"package_sources\[1\].checkout.*separate from every other package source")

    def test_a_missing_package_directory_is_rejected(self) -> None:
        self.entry["packages"] = ["site-board", "absent-package"]
        self.reject(r"package_sources\[0\].packages\[absent-package\].*does not exist")

    def test_an_empty_package_list_is_rejected(self) -> None:
        # A source that supplies nothing is a checkout hydrated for no reason.
        self.entry["packages"] = []
        self.reject(r"package_sources\[0\].packages")

    def test_a_repeated_package_name_within_one_source_is_rejected(self) -> None:
        self.entry["packages"] = ["site-board", "site-board"]
        self.reject(r"package_sources\[0\].packages.*duplicate")

    def test_a_package_name_also_supplied_by_the_platform_is_rejected(self) -> None:
        self.entry["packages"] = [self.declaration["deployment"][0]["packages"]["platform"][0]]
        self._materialise()
        self.reject(r"\.packages.*duplicate")

    def test_a_package_name_carrying_whitespace_is_rejected(self) -> None:
        # Package names cross a whitespace-separated transport into the launch contract.
        self.entry["packages"] = ["site board"]
        self.reject(r"package_sources\[0\].packages.*whitespace")

    def test_a_non_array_declaration_is_rejected(self) -> None:
        self.declaration["deployment"][0]["package_sources"] = {"lock_ref": "package-source"}
        self.reject(r"package_sources.*array of tables")


if __name__ == "__main__":
    unittest.main()
