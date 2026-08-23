from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from schema.validator import (
    ValidationError,
    domain_a_normalized_login,
    load_and_resolve,
    normalized_login,
    validate_and_resolve,
)


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
        tool = root / "make"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(0o755)
        self.machine["tools"] = {"make": str(tool)}
        self._prepare_deployment_paths(self.declaration)

    def _prepare_deployment_paths(self, declaration: dict) -> None:
        for deployment in declaration["deployment"]:
            platform = Path(self.machine["roots"][deployment["machine"]["platform_checkout"]])
            engine = Path(self.machine["roots"][deployment["machine"]["engine_checkout"]])
            for package in deployment["packages"]["platform"]:
                (platform / "packages" / package).mkdir(parents=True, exist_ok=True)
            for entry in deployment.get("package_sources", []):
                root = Path(self.machine["roots"][entry["checkout"]])
                for package in entry["packages"]:
                    (root / "packages" / package).mkdir(parents=True, exist_ok=True)
            target = Path(self.machine["roots"][deployment["machine"]["target_checkout"]])
            entries = {"target-source": target, "platform-source": platform, "engine-source": engine}
            for provider in declaration["provider"]:
                lock_ref, relative = provider["implementation"].split(":", 1)
                if lock_ref == "fkst-ops":
                    continue
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
        self.assertEqual(
            website["deployment"][0]["packages"],
            {"platform": ["github-devloop", "github-devloop-pr", "github-devloop-integration"]},
        )
        # The website topology is the one that carries no packages inside its target: its Lua
        # comes from a second package source named by the declaration.
        self.assertEqual(
            [(entry["lock_ref"], entry["packages"]) for entry in website["deployment"][0]["package_sources"]],
            [("package-source", ["site-board"])],
        )
        self.assertTrue(website["deployment"][0]["package_sources"][0]["checkout"].startswith("/"))

    def test_retired_host_package_field_is_rejected_with_its_location(self) -> None:
        for value in ([], ["site-board"]):
            with self.subTest(value=value):
                declaration = copy.deepcopy(self.declaration)
                declaration["deployment"][0]["packages"]["host"] = value
                with self.assertRaisesRegex(
                    ValidationError,
                    r"declaration\.deployment\[0\]\.packages: unknown field: host",
                ):
                    validate_and_resolve(declaration, self.machine, self.lock)

    def test_platform_package_list_is_required_with_its_location(self) -> None:
        del self.declaration["deployment"][0]["packages"]["platform"]
        self.reject(
            r"declaration\.deployment\[0\]\.packages\.platform: "
            r"must be a non-empty string list"
        )

    def test_a_declaration_still_carrying_the_retired_write_field_resolves(self) -> None:
        """Accepted and ignored, so the two repositories need not merge in the same instant.

        The field selects nothing — writing is unconditional — so tolerating it is not a second
        behaviour. Requiring its absence would reject every live declaration until fkst-deployments
        merged, and this machine adopts an fkst-ops merge with no pin advance.
        """
        for value in (True, False):
            with self.subTest(value=value):
                declaration = copy.deepcopy(self.declaration)
                declaration["deployment"][0]["github_write_enabled"] = value
                resolved = validate_and_resolve(declaration, self.machine, self.lock)
                self.assertNotIn("github_write_enabled", resolved["deployment"][0])

    def test_machine_default_reference_resolves(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(result["deployment"][0]["integration"]["integration_branch"], "integration")

    def test_local_test_command_is_optional_and_preserved_exactly(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertNotIn("local_test_command", result["deployment"][0]["integration"])

        command = "npm run check -- --mode ci"
        self.declaration["deployment"][0]["integration"]["local_test_command"] = command
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            command,
            result["deployment"][0]["integration"]["local_test_command"],
        )

    def test_github_credential_source_is_required_without_a_default(self) -> None:
        del self.declaration["deployment"][0]["providers"]["github_credential"]
        self.reject("providers.github_credential.*non-empty string")

    def test_cadence_interval_is_required_and_positive(self) -> None:
        del self.declaration["cadence_interval_seconds"]
        self.reject("cadence_interval_seconds.*positive integer")

    def test_cadence_enablement_is_required_without_a_default(self) -> None:
        del self.declaration["cadence_enabled"]
        self.reject("cadence_enabled.*boolean")

    def test_guard_restart_attempt_limit_is_required_without_a_default(self) -> None:
        del self.declaration["guard_restart_attempt_limit"]
        self.reject("guard_restart_attempt_limit.*non-negative integer")

    def test_guard_restart_attempt_limit_accepts_zero_and_resolves(self) -> None:
        self.declaration["guard_restart_attempt_limit"] = 0
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(result["guard_restart_attempt_limit"], 0)

    def test_guard_restart_attempt_limit_rejects_nonintegers_and_negative_values(self) -> None:
        for value in (-1, True, 1.5, "3"):
            with self.subTest(value=value):
                self.declaration["guard_restart_attempt_limit"] = value
                self.reject("guard_restart_attempt_limit.*non-negative integer")

    def test_claim_posture_is_required_and_closed(self) -> None:
        del self.declaration["deployment"][0]["claim_posture"]
        self.reject("claim_posture.*must be a table")

    def test_claim_posture_resolves_exactly(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            result["deployment"][0]["claim_posture"],
            {"mode": "label", "label_exclusive": False},
        )

    def test_claim_posture_rejects_implicit_or_invalid_values(self) -> None:
        del self.declaration["deployment"][0]["claim_posture"]["label_exclusive"]
        self.reject("claim_posture.label_exclusive.*boolean")

    def test_two_bot_roster_accepts_each_member_as_machine_actor(self) -> None:
        roster = ["bot-a", "bot-b"]
        self.declaration["deployment"][0]["managed_bot_logins"] = roster
        for actor in roster:
            with self.subTest(actor=actor):
                machine = copy.deepcopy(self.machine)
                machine["credentials"]["github-bot"] = actor
                resolved = validate_and_resolve(self.declaration, machine, self.lock)
                deployment = resolved["deployment"][0]
                self.assertEqual(deployment["machine"]["bot_login"], actor)
                self.assertEqual(deployment["managed_bot_logins"], roster)

    def test_nonmember_machine_actor_fails_closed(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = ["bot-a", "bot-b"]
        self.machine["credentials"]["github-bot"] = "bot-c"
        self.reject("resolved bot login must belong to deployment.managed_bot_logins")

    def test_bot_membership_strips_bot_suffix_without_changing_case(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = ["Managed-Bot"]
        self.machine["credentials"]["github-bot"] = "Managed-Bot[bot]"
        resolved = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            resolved["deployment"][0]["machine"]["bot_login"],
            "Managed-Bot[bot]",
        )

        self.machine["credentials"]["github-bot"] = "managed-bot[bot]"
        self.reject("resolved bot login must belong to deployment.managed_bot_logins")

    def test_bot_roster_uniqueness_uses_normalized_case_sensitive_login(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = [
            "Y[BOT]",
            "Y[BOT][bot]",
        ]
        self.reject("duplicate normalized identity")

        self.declaration["deployment"][0]["managed_bot_logins"] = ["X", "x-bot[bot]"]
        self.machine["credentials"]["github-bot"] = "X[bot]"
        resolved = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            resolved["deployment"][0]["managed_bot_logins"], ["X", "x-bot[bot]"]
        )

    def test_bot_roster_rejects_domain_a_case_fold_collapse_with_indices(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = [
            "Local-Bot",
            "local-bot",
        ]
        self.reject(
            r"managed_bot_logins: cross-domain collapse under domain A: entries "
            r"\[0\] and \[1\]"
        )

    def test_authorized_logins_rejects_domain_a_case_fold_collapse_with_indices(self) -> None:
        self.declaration["deployment"][0]["author_authorization"][
            "authorized_logins"
        ] = ["Trusted-Author", "trusted-author[bot]"]
        self.reject(
            r"authorized_logins: cross-domain collapse under domain A: entries "
            r"\[0\] and \[1\]"
        )

    def test_machine_actor_rejects_domain_a_alias_to_non_self_roster_entry(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = [
            "Y[BOT][bot]",
            "y",
        ]
        self.machine["credentials"]["github-bot"] = "Y[BOT]"
        self.reject(
            r"bot_login 'Y\[BOT\]' aliases non-self roster entry \[1\] 'y' as 'y'"
        )

    def test_machine_actor_can_match_its_own_domain_a_shaped_roster_entry(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = [
            "Y[BOT]",
            "peer-bot",
        ]
        self.machine["credentials"]["github-bot"] = "Y[BOT]"
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(result["deployment"][0]["machine"]["bot_login"], "Y[BOT]")

    def test_approved_roster_is_unique_in_domain_a_but_domains_can_disagree(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = ["Managed-Bot"]
        self.machine["credentials"]["github-bot"] = "Managed-Bot"
        resolved = validate_and_resolve(self.declaration, self.machine, self.lock)
        approved = resolved["deployment"][0]["managed_bot_logins"]
        self.assertEqual(
            len({domain_a_normalized_login(login) for login in approved}),
            len(approved),
        )
        inbound_author = "managed-bot[bot]"
        self.assertIn(
            domain_a_normalized_login(inbound_author),
            {domain_a_normalized_login(login) for login in approved},
        )
        self.assertNotIn(
            normalized_login(inbound_author),
            {normalized_login(login) for login in approved},
        )

    def test_selected_complete_fleet_invariant_rejects_peers_only_roster(self) -> None:
        self.declaration["deployment"][0]["managed_bot_logins"] = ["peer-bot"]
        self.machine["credentials"]["github-bot"] = "local-bot"
        self.reject("resolved bot login must belong to deployment.managed_bot_logins")

    def test_platform_login_lists_reject_tokenizer_delimiters_with_item_path(self) -> None:
        cases = (
            ("comma", "trusted,attacker"),
            ("space", "trusted attacker"),
            ("tab", "trusted\tattacker"),
            ("newline", "trusted\nattacker"),
            ("carriage-return", "trusted\rattacker"),
            ("vertical-tab", "trusted\vattacker"),
            ("form-feed", "trusted\fattacker"),
            ("unicode-whitespace", "trusted\u00a0attacker"),
            ("nul", "trusted\x00attacker"),
        )
        fields = (
            ("managed_bot_logins", self.declaration["deployment"][0]),
            (
                "authorized_logins",
                self.declaration["deployment"][0]["author_authorization"],
            ),
        )
        for field, table in fields:
            for delimiter, login in cases:
                with self.subTest(field=field, delimiter=delimiter):
                    table[field] = [login]
                    with self.assertRaises(ValidationError) as raised:
                        validate_and_resolve(self.declaration, self.machine, self.lock)
                    self.assertIn(f".{field}[0]:", str(raised.exception))
                    self.assertIn(
                        "without commas, whitespace, or NUL", str(raised.exception)
                    )
            table[field] = ["fkst-bot"] if field == "managed_bot_logins" else []

    def test_platform_login_lists_reject_empty_normalized_identity_with_item_path(self) -> None:
        fields = (
            ("managed_bot_logins", self.declaration["deployment"][0]),
            (
                "authorized_logins",
                self.declaration["deployment"][0]["author_authorization"],
            ),
        )
        for field, table in fields:
            with self.subTest(field=field):
                table[field] = ["[bot]"]
                with self.assertRaises(ValidationError) as raised:
                    validate_and_resolve(self.declaration, self.machine, self.lock)
                self.assertIn(f".{field}[0]:", str(raised.exception))
                self.assertIn("must not normalize to an empty identity", str(raised.exception))
            table[field] = ["fkst-bot"] if field == "managed_bot_logins" else []

    def test_resolved_machine_actor_rejects_empty_normalized_identity(self) -> None:
        self.machine["credentials"]["github-bot"] = "[bot]"
        self.reject(
            r"declaration.deployment\[0\].machine.bot_login: "
            "must not normalize to an empty identity"
        )

    def test_domain_a_empty_normalized_identity_is_rejected_everywhere(self) -> None:
        for field, table in (
            ("managed_bot_logins", self.declaration["deployment"][0]),
            ("authorized_logins", self.declaration["deployment"][0]["author_authorization"]),
        ):
            with self.subTest(field=field):
                table[field] = ["[BOT]"]
                with self.assertRaisesRegex(ValidationError, "must not normalize to an empty identity"):
                    validate_and_resolve(self.declaration, self.machine, self.lock)
            table[field] = ["fkst-bot"] if field == "managed_bot_logins" else []

        self.machine["credentials"]["github-bot"] = "[BOT]"
        self.reject(
            r"declaration\.deployment\[0\]\.machine\.bot_login: "
            "must not normalize to an empty identity"
        )

    def test_legacy_machine_managed_bot_set_fails_closed_as_unknown(self) -> None:
        self.declaration["deployment"][0]["machine"]["managed_bot_set"] = "managed-bots"
        self.reject("unknown field: managed_bot_set")

    def test_author_authorization_resolves_declared_policy(self) -> None:
        self.declaration["deployment"][0]["author_authorization"] = {
            "authorized_logins": ["trusted-author"],
            "authorize_org_members": True,
            "authorize_repo_collaborators": True,
        }
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            result["deployment"][0]["author_authorization"],
            {
                "authorized_logins": ["trusted-author"],
                "authorize_org_members": True,
                "authorize_repo_collaborators": True,
            },
        )

    def test_author_authorization_defaults_to_no_additional_authors(self) -> None:
        del self.declaration["deployment"][0]["author_authorization"]
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            result["deployment"][0]["author_authorization"],
            {
                "authorized_logins": [],
                "authorize_org_members": False,
                "authorize_repo_collaborators": False,
            },
        )

    def test_author_authorization_rejects_malformed_policy(self) -> None:
        self.declaration["deployment"][0]["author_authorization"] = {
            "authorized_logins": "trusted-author",
            "authorize_org_members": True,
            "authorize_repo_collaborators": False,
        }
        self.reject("author_authorization.authorized_logins.*string list")

    def test_source_git_url_resolves_from_lock(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        source = result["deployment"][0]["sources"]["platform"]
        self.assertEqual(source["git"], "https://invalid.example/target.git")
        engine = result["deployment"][0]["sources"]["engine"]
        self.assertEqual(engine["git"], "https://invalid.example/engine.git")

    def test_engine_revision_derivation_resolves_without_deployment_source_pins(self) -> None:
        result = validate_and_resolve(self.declaration, self.machine, self.lock)

        deployment = result["deployment"][0]
        self.assertEqual(
            deployment["engine_revision"],
            {"path": ".control/engine-ref"},
        )
        self.assertNotIn("pin", deployment["sources"]["engine"])

    def test_deployment_operated_source_accepts_optional_resolved_pin(self) -> None:
        self.lock["external_source"][0]["resolved"] = {
            "rev": "1" * 40,
        }
        resolved = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertEqual(
            resolved["deployment"][0]["sources"]["target"]["resolved"],
            self.lock["external_source"][0]["resolved"],
        )

    def test_mechanism_source_still_requires_resolved_pin(self) -> None:
        mechanism = next(
            entry for entry in self.lock["external_source"]
            if entry["checkout_role"] == "mechanism"
        )
        del mechanism["resolved"]
        self.reject("resolved.*must be a table")

    def test_engine_revision_has_no_literal_revision_input(self) -> None:
        self.declaration["deployment"][0]["engine_revision"]["revision"] = "1" * 40
        self.reject("engine_revision.*unknown field: revision")

    def test_engine_revision_path_rejects_checkout_escape(self) -> None:
        self.declaration["deployment"][0]["engine_revision"]["path"] = "../engine-ref"
        self.reject("DERIVATION_PATH_INVALID")

    def test_engine_revision_rejects_removed_checkout_tag(self) -> None:
        self.declaration["deployment"][0]["engine_revision"]["checkout"] = "platform"
        self.reject("engine_revision.*unknown field: checkout")

    def test_engine_checkout_cannot_alias_branch_operated_checkout(self) -> None:
        self.declaration["deployment"][0]["machine"]["engine_checkout"] = "packages-host"
        self.reject("must be separate from branch-operated target and platform checkouts")

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
        self.assertEqual(
            deployment["providers"]["engine"]["configuration"]["build_command"],
            [self.machine["tools"]["make"], "engine"],
        )

    def test_github_credential_source_resolves_from_machine_default(self) -> None:
        provider = next(
            item for item in self.declaration["provider"]
            if item["kind"] == "credential.github"
        )
        provider["configuration"]["source"] = "machine:github-credential-source"
        self.machine["defaults"]["github-credential-source"] = "github-cli-user"

        result = validate_and_resolve(self.declaration, self.machine, self.lock)

        resolved = result["deployment"][0]["providers"]["github_credential"]
        self.assertEqual(resolved["configuration"], {"source": "github-cli-user"})

    def test_github_credential_source_rejects_unresolved_machine_default(self) -> None:
        provider = next(
            item for item in self.declaration["provider"]
            if item["kind"] == "credential.github"
        )
        provider["configuration"]["source"] = "machine:missing-source"

        self.reject(
            r"declaration\.provider\[3\]\.configuration\.source: "
            r"unresolved logical defaults reference: missing-source"
        )

    def test_github_credential_source_rejects_invalid_resolved_value(self) -> None:
        provider = next(
            item for item in self.declaration["provider"]
            if item["kind"] == "credential.github"
        )
        provider["configuration"]["source"] = "machine:github-credential-source"
        self.machine["defaults"]["github-credential-source"] = "ambient-account"

        self.reject(
            r"declaration\.provider\[3\]\.configuration\.source: "
            r"must be github-app or github-cli-user"
        )

    def test_literal_github_app_credential_source_resolves_unchanged(self) -> None:
        before = copy.deepcopy(self.declaration)

        result = validate_and_resolve(self.declaration, self.machine, self.lock)

        resolved = result["deployment"][0]["providers"]["github_credential"]
        self.assertEqual(resolved["configuration"], {"source": "github-app"})
        self.assertEqual(self.declaration, before)

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
        for field in ("rate_pool", "bot_login"):
            del self.declaration["deployment"][0]["machine"][field]
        result = validate_and_resolve(self.declaration, self.machine, self.lock)
        self.assertNotIn("rate_pool", result["deployment"][0]["machine"])
        self.assertNotIn("bot_login", result["deployment"][0]["machine"])

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
        mechanism = next(
            entry for entry in self.lock["external_source"]
            if entry["checkout_role"] == "mechanism"
        )
        del mechanism["resolved"]["rev"]
        self.reject("resolved.rev.*non-empty string")

    def test_resolved_pin_accepts_only_rev(self) -> None:
        mechanism = next(
            entry for entry in self.lock["external_source"]
            if entry["checkout_role"] == "mechanism"
        )
        self.assertNotIn("tree_sha256", mechanism["resolved"])
        validate_and_resolve(self.declaration, self.machine, self.lock)

        mechanism["resolved"]["tree_sha256"] = "sha256-" + "1" * 64
        self.reject(r"resolved: unknown field: tree_sha256")

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

    def test_rejects_whitespace_and_control_characters_in_machine_roots(self) -> None:
        for label, value in (
            ("space", "/srv/package source"),
            ("tab", "/srv/package\tsource"),
            ("carriage-return", "/srv/package\rsource"),
            ("newline", "/srv/package\nsource"),
            ("nul", "/srv/package\x00source"),
        ):
            with self.subTest(label=label):
                self.machine["roots"]["packages-host"] = value
                self.reject(
                    r"machine_profile\.roots\.packages-host: "
                    r"must not contain control characters or whitespace"
                )

    def test_rejects_whitespace_and_control_characters_in_machine_binaries(self) -> None:
        for label, suffix in (("space", " "), ("tab", "\t"), ("newline", "\n")):
            with self.subTest(label=label):
                self.machine["binaries"]["engine"] += suffix
                self.reject(
                    r"machine_profile\.binaries\.engine: "
                    r"must not contain control characters or whitespace"
                )

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

    def test_rejects_package_source_name_outside_engine_grammar(self) -> None:
        declaration = load("website.toml")
        declaration["deployment"][0]["package_sources"][0]["packages"] = ["site.board"]
        self._prepare_deployment_paths(declaration)
        self.declaration = declaration

        self.reject(r"package name must match \[A-Za-z0-9_-\]\+")

    def test_rejects_reserved_host_package_source_name(self) -> None:
        declaration = load("website.toml")
        declaration["deployment"][0]["package_sources"][0]["packages"] = ["host"]
        self._prepare_deployment_paths(declaration)
        self.declaration = declaration

        self.reject("package name 'host' is reserved")

    def test_rejects_ere_syntax_before_the_staleness_probe(self) -> None:
        declaration = load("website.toml")
        declaration["deployment"][0]["package_sources"][0]["packages"] = ["site.*"]
        self._prepare_deployment_paths(declaration)
        self.declaration = declaration

        self.reject(r"package name must match \[A-Za-z0-9_-\]\+")

    def test_rejects_package_source_parent_traversal_even_when_target_exists(self) -> None:
        declaration = load("website.toml")
        package_source = declaration["deployment"][0]["package_sources"][0]
        package_source["packages"] = ["../../outside"]
        source_root = Path(self.machine["roots"][package_source["checkout"]])
        escaped_target = (source_root / "packages" / "../../outside").resolve()
        self._prepare_deployment_paths(declaration)
        self.assertTrue(escaped_target.is_dir())
        self.declaration = declaration

        self.reject(r"package name must match \[A-Za-z0-9_-\]\+")

    def test_rejects_package_source_symlink_outside_its_checkout(self) -> None:
        declaration = load("website.toml")
        package_source = declaration["deployment"][0]["package_sources"][0]
        self._prepare_deployment_paths(declaration)
        source_root = Path(self.machine["roots"][package_source["checkout"]])
        package_root = source_root / "packages" / package_source["packages"][0]
        package_root.rmdir()
        outside = Path(self.temp.name) / "outside-package"
        outside.mkdir()
        package_root.symlink_to(outside, target_is_directory=True)
        self.declaration = declaration

        self.reject("package root must be a direct child of its declared source")

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
