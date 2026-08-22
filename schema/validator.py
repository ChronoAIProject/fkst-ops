"""Fail-closed validator for fkst.ops.deployment.v1 declarations."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tomllib
import unicodedata
from typing import Any, NoReturn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema.provider_surface import PUBLISHED_PROVIDER_ENTRY_POINTS
from ops.revision_derivation import RevisionDerivationError, validate_derivation_path


SCHEMA_ID = "fkst.ops.deployment.v1"
MACHINE_SCHEMA_ID = "fkst.ops.machine-profile.v1"
CONTRACTS = {
    "credential.github": "fkst.ops.credential.github.v1",
    "engine": "fkst.ops.engine.v1",
    "board.engine-durable": "fkst.ops.board.engine-durable.v1",
    "board.github-control": "fkst.ops.board.github-control.v1",
}
PROVIDER_FIELDS = {
    "github_credential": "credential.github",
    "engine": "engine",
    "board_engine_durable": "board.engine-durable",
    "board_github_control": "board.github-control",
}
GITHUB_CREDENTIAL_SOURCES = ("github-app", "github-cli-user")
MACHINE_KINDS = {
    "target_checkout": "roots",
    "platform_checkout": "roots",
    "engine_checkout": "roots",
    "engine_binary": "binaries",
    "durable": "roots",
    "runtime": "roots",
    "logs": "roots",
    "rate_pool": "roots",
    "bot_login": "credentials",
}
PROFILE_MACHINE_FIELDS = {"rate_pool", "bot_login"}
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class ValidationError(ValueError):
    """A complete preflight validation failure."""


def _fail(path: str, message: str) -> NoReturn:
    raise ValidationError(f"{path}: {message}")


def _table(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be a table")
    return value


def _closed(table: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        _fail(path, f"unknown field: {unknown[0]}")


def _string(table: dict[str, Any], field: str, path: str) -> str:
    value = table.get(field)
    if not isinstance(value, str) or not value:
        _fail(f"{path}.{field}", "must be a non-empty string")
    return value


def _string_list(table: dict[str, Any], field: str, path: str, *, nonempty: bool) -> list[str]:
    value = table.get(field)
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{path}.{field}", "must be a non-empty string list" if nonempty else "must be a string list")
    if any(not isinstance(item, str) or not item for item in value):
        _fail(f"{path}.{field}", "must contain only non-empty strings")
    return value


def normalized_login(login: str) -> str:
    """Match workflow_board_fact.py's case-sensitive managed-login identity."""
    # Keep this exactly aligned with
    # packages/github-devloop-workflow/tools/workflow_board_fact.py:normalized_login.
    return login[:-5] if login.endswith("[bot]") else login


def domain_a_normalized_login(login: str) -> str:
    """Match content_filter.lua's case-insensitive authorization identity."""
    # Keep this aligned with libraries/forge/github/content_filter.lua:370-380.
    value = login.strip().lower()
    return value[:-5] if value.endswith("[bot]") else value


def validate_platform_login(login: Any, path: str) -> str:
    """Validate one identity across the shell and platform-tokenizer boundary."""
    if not isinstance(login, str) or not login:
        _fail(path, "must be a non-empty string")
    if re.search(r"[,\s\x00]", login):
        _fail(
            path,
            "must be a single platform token without commas, whitespace, or NUL characters",
        )
    if not normalized_login(login) or not domain_a_normalized_login(login):
        _fail(path, "must not normalize to an empty identity")
    return login


def _platform_login_list(
    table: dict[str, Any], field: str, path: str, *, nonempty: bool
) -> list[str]:
    logins = _string_list(table, field, path, nonempty=nonempty)
    # Keep this trust boundary aligned with the shell transport and platform tokenizers in
    # packages/github-devloop-workflow/tools/workflow_board_fact.py:79 and
    # packages/github-external-pr-intake/core.lua:64.
    for index, login in enumerate(logins):
        validate_platform_login(login, f"{path}.{field}[{index}]")
    return logins


def _positive_integer(table: dict[str, Any], field: str, path: str) -> int:
    value = table.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _fail(f"{path}.{field}", "must be a positive integer")
    return value


def _nonnegative_integer(table: dict[str, Any], field: str, path: str) -> int:
    value = table.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(f"{path}.{field}", "must be a non-negative integer")
    return value


def _logical(value: str, path: str) -> None:
    if os.path.isabs(value) or PurePosixPath(value).is_absolute():
        _fail(path, "absolute machine value is forbidden; use a logical name")
    if "/" in value or "\\" in value:
        _fail(path, "must be a logical name, not a path")


def machine_default_reference(value: str, path: str) -> str | None:
    """Return and validate a logical ``machine:`` defaults reference, if present."""
    if not value.startswith("machine:"):
        return None
    logical = value.removeprefix("machine:")
    _logical(logical, path)
    return logical


def resolve_machine_default(value: str, defaults: dict[str, str], path: str) -> str:
    """Resolve one declaration value through a machine profile's defaults table."""
    logical = machine_default_reference(value, path)
    if logical is None:
        return value
    if logical not in defaults:
        _fail(path, f"unresolved logical defaults reference: {logical}")
    return defaults[logical]


def _validate_lock(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _closed(lock, {"external_source"}, "lock")
    entries = lock.get("external_source")
    if not isinstance(entries, list) or not entries:
        _fail("lock.external_source", "must be a non-empty array of tables")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(entries):
        path = f"lock.external_source[{index}]"
        entry = _table(raw, path)
        _closed(entry, {"id", "git", "intent", "checkout_role", "resolved", "libraries"}, path)
        identity = _string(entry, "id", path)
        _string(entry, "git", path)
        checkout_role = _string(entry, "checkout_role", path)
        if checkout_role not in {"deployment-operated", "mechanism"}:
            _fail(path + ".checkout_role", "must be deployment-operated or mechanism")
        if checkout_role == "mechanism" or "resolved" in entry:
            resolved = _table(entry.get("resolved"), f"{path}.resolved")
            _closed(resolved, {"rev"}, f"{path}.resolved")
            rev = _string(resolved, "rev", f"{path}.resolved")
            if not _SHA.fullmatch(rev):
                _fail(f"{path}.resolved.rev", "must be a full lowercase Git SHA")
        if identity in result:
            _fail(path + ".id", f"duplicate lock identity: {identity}")
        result[identity] = entry
    return result


def _validate_machine_profile(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    allowed = {"schema", "roots", "binaries", "tools", "credentials", "sets", "defaults"}
    _closed(profile, allowed, "machine_profile")
    if _string(profile, "schema", "machine_profile") != MACHINE_SCHEMA_ID:
        _fail("machine_profile.schema", f"must be {MACHINE_SCHEMA_ID}")
    result: dict[str, dict[str, Any]] = {}
    for kind in allowed - {"schema"}:
        values = _table(profile.get(kind, {}), f"machine_profile.{kind}")
        for name, value in values.items():
            if not isinstance(name, str) or not name:
                _fail(f"machine_profile.{kind}", "names must be non-empty strings")
            if kind == "sets":
                if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                    _fail(f"machine_profile.{kind}.{name}", "must be a string list")
            elif not isinstance(value, str) or not value:
                _fail(f"machine_profile.{kind}.{name}", "must be a non-empty string")
            if kind in {"roots", "binaries"} and any(
                character.isspace() or unicodedata.category(character) == "Cc"
                for character in value
            ):
                _fail(
                    f"machine_profile.{kind}.{name}",
                    "must not contain control characters or whitespace",
                )
            if kind in {"roots", "binaries", "tools"} and not os.path.isabs(value):
                _fail(f"machine_profile.{kind}.{name}", "must be an absolute path")
        result[kind] = values
    return result


def declared_external_tools(declaration: dict[str, Any]) -> set[str]:
    """Return bare executables named by provider command configuration fields."""
    tools: set[str] = set()
    for provider in declaration.get("provider", []):
        if not isinstance(provider, dict):
            continue
        configuration = provider.get("configuration", {})
        if not isinstance(configuration, dict):
            continue
        for field, command in configuration.items():
            if field.endswith("_command") and isinstance(command, list) and command:
                executable = command[0]
                if isinstance(executable, str) and executable and Path(executable).name == executable:
                    tools.add(executable)
    return tools


def _resolve_provider_commands(
    providers: dict[str, dict[str, Any]], tools: dict[str, Any]
) -> None:
    for provider in providers.values():
        configuration = provider["configuration"]
        for field, command in configuration.items():
            if not field.endswith("_command") or not isinstance(command, list) or not command:
                continue
            executable = command[0]
            if Path(executable).name != executable:
                continue
            if executable not in tools:
                _fail(
                    f"machine_profile.tools.{executable}",
                    f"missing discovered tool location for declared executable: {executable}",
                )
            resolved = _require_executable(
                tools[executable], f"machine_profile.tools.{executable}"
            )
            command[0] = str(resolved)


def _resolve_machine(
    machine: dict[str, Any], profile: dict[str, dict[str, Any]], path: str, *, profile_present: bool
) -> dict[str, Any]:
    _closed(machine, set(MACHINE_KINDS), path)
    resolved: dict[str, Any] = {}
    for field, kind in MACHINE_KINDS.items():
        if field not in machine and field in PROFILE_MACHINE_FIELDS and not profile_present:
            continue
        name = _string(machine, field, path)
        _logical(name, f"{path}.{field}")
        if name not in profile[kind]:
            _fail(f"{path}.{field}", f"unresolved logical {kind} reference: {name}")
        resolved[field] = copy.deepcopy(profile[kind][name])
        if field == "bot_login":
            validate_platform_login(resolved[field], f"{path}.{field}")
    return resolved


def _require_directory(value: str, path: str) -> Path:
    target = Path(value)
    if not target.is_dir():
        _fail(path, f"resolved root does not exist: {value}")
    return target


def _require_executable(value: str, path: str) -> Path:
    target = Path(value)
    if not target.is_file() or not os.access(target, os.X_OK):
        _fail(path, f"resolved executable does not exist or is not executable: {value}")
    return target


def _require_unique(values: list[str], path: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            _fail(path, f"duplicate identity: {value}")
        seen.add(value)


def _validate_package_name(value: str, path: str) -> None:
    if not _PACKAGE_NAME.fullmatch(value):
        _fail(
            path,
            "package name must match [A-Za-z0-9_-]+; whitespace and other characters are forbidden",
        )
    if value == "host":
        _fail(path, "package name 'host' is reserved")


def _require_unique_logins(values: list[str], path: str) -> None:
    seen: set[str] = set()
    for value in values:
        identity = normalized_login(value)
        if identity in seen:
            _fail(path, f"duplicate normalized identity: {value}")
        seen.add(identity)


def _require_no_domain_a_collapse(values: list[str], path: str) -> None:
    """Reject roster entries that collapse in the platform's coarsest domain."""
    seen: dict[str, int] = {}
    for index, value in enumerate(values):
        identity = domain_a_normalized_login(value)
        previous = seen.get(identity)
        if previous is not None:
            _fail(
                path,
                "cross-domain collapse under domain A: entries "
                f"[{previous}] and [{index}] both normalize to {identity!r}",
            )
        seen[identity] = index


def _require_actor_no_domain_a_collapse(
    bot_login: str, roster: list[str], path: str
) -> None:
    """Reject an actor that aliases a different roster member in domain A."""
    actor_domain_b = normalized_login(bot_login)
    actor_domain_a = domain_a_normalized_login(bot_login)
    for index, value in enumerate(roster):
        # Domain-B membership defines which roster entry represents this actor.
        if normalized_login(value) == actor_domain_b:
            continue
        if domain_a_normalized_login(value) == actor_domain_a:
            _fail(
                path,
                "cross-domain collapse under domain A: bot_login "
                f"{bot_login!r} aliases non-self roster entry [{index}] "
                f"{value!r} as {actor_domain_a!r}",
            )


def _validate_resolved_paths(resolved: dict[str, Any], path: str, pins: dict[str, dict[str, Any]]) -> None:
    machine = resolved["machine"]
    checkouts = {
        role: _require_directory(machine[f"{role}_checkout"], f"{path}.machine.{role}_checkout")
        for role in ("target", "platform", "engine")
    }
    engine_root = checkouts["engine"].resolve()
    if any(engine_root == checkouts[role].resolve() for role in ("target", "platform")):
        _fail(
            path + ".machine.engine_checkout",
            "must be separate from branch-operated target and platform checkouts",
        )
    _require_directory(machine["durable"], path + ".machine.durable")

    for package in resolved["packages"]["platform"]:
        _require_directory(str(checkouts["platform"] / "packages" / package), path + f".packages.platform[{package}]")
    for package in resolved["packages"]["host"]:
        _require_directory(str(checkouts["target"] / ".fkst" / "local-packages" / package), path + f".packages.host[{package}]")

    package_source_roots: list[Path] = []
    for entry_index, entry in enumerate(resolved.get("package_sources", [])):
        entry_path = f"{path}.package_sources[{entry_index}]"
        root = _require_directory(entry["checkout"], entry_path + ".checkout")
        resolved_root = root.resolve()
        for role in ("target", "platform", "engine"):
            if resolved_root == checkouts[role].resolve():
                _fail(entry_path + ".checkout", f"must be separate from the {role} checkout")
        if resolved_root in package_source_roots:
            _fail(entry_path + ".checkout", "must be separate from every other package source checkout")
        package_source_roots.append(resolved_root)
        packages_root = (resolved_root / "packages").resolve()
        for package in entry["packages"]:
            package_root = (packages_root / package).resolve()
            if packages_root.parent != resolved_root or package_root.parent != packages_root:
                _fail(
                    entry_path + f".packages[{package}]",
                    "package root must be a direct child of its declared source",
                )
            _require_directory(str(package_root), entry_path + f".packages[{package}]")

    source_roots: dict[str, set[Path]] = {}
    for role, source in resolved["sources"].items():
        lock_ref = source["lock_ref"]
        root = checkouts[role]
        source_roots.setdefault(lock_ref, set()).add(root.resolve())
    # bootstrap/run.sh verifies the explicitly declared mechanism checkout before
    # handing control to the validator. Its identity is not inferred from its id.
    for lock_ref, pin in pins.items():
        if pin["checkout_role"] == "mechanism":
            source_roots[lock_ref] = {Path(__file__).resolve().parents[1]}
    for field, provider in resolved["providers"].items():
        lock_ref, relative = provider["implementation"].split(":", 1)
        roots = source_roots.get(lock_ref)
        if roots is None:
            _fail(path + f".providers.{field}", f"provider source is not bound to a deployment or mechanism source: {lock_ref}")
        if len(roots) != 1:
            _fail(
                path + f".providers.{field}.implementation",
                f"provider source {lock_ref} is ambiguous across declared checkouts",
            )
        root = next(iter(roots))
        if pins[lock_ref]["checkout_role"] == "mechanism":
            published_kind = PUBLISHED_PROVIDER_ENTRY_POINTS.get(relative)
            if published_kind != provider["kind"]:
                _fail(path + f".providers.{field}.implementation", "entry point is not published for this provider kind")
        executable = (root / relative).resolve()
        try:
            executable.relative_to(root.resolve())
        except ValueError:
            _fail(path + f".providers.{field}", "provider entry point escapes its source checkout")
        _require_executable(str(executable), path + f".providers.{field}.implementation")
        provider["executable"] = str(executable)


def validate_and_resolve(
    declaration: dict[str, Any],
    machine_profile: dict[str, Any],
    lock: dict[str, Any],
    *,
    require_materialized_paths: bool = True,
) -> dict[str, Any]:
    """Validate all inputs and return a newly allocated resolved declaration.

    Generation uses ``require_materialized_paths=False`` for its pre-hydration gate. That skips
    only checks whose subjects do not exist until hydration: checkout/package directories and
    provider executables within those checkouts. Every declared value and topology invariant is
    still validated in that pass; the default post-hydration pass proves the filesystem facts.
    """
    declaration = _table(declaration, "declaration")
    _closed(
        declaration,
        {
            "schema",
            "cadence_enabled",
            "cadence_interval_seconds",
            "guard_restart_attempt_limit",
            "deployment",
            "provider",
        },
        "declaration",
    )
    if _string(declaration, "schema", "declaration") != SCHEMA_ID:
        _fail("declaration.schema", f"must be {SCHEMA_ID}")
    cadence_interval_seconds = _positive_integer(
        declaration, "cadence_interval_seconds", "declaration"
    )
    guard_restart_attempt_limit = _nonnegative_integer(
        declaration, "guard_restart_attempt_limit", "declaration"
    )
    cadence_enabled = declaration.get("cadence_enabled")
    if not isinstance(cadence_enabled, bool):
        _fail("declaration.cadence_enabled", "must be a boolean")
    pins = _validate_lock(_table(lock, "lock"))
    machine_values = _validate_machine_profile(_table(machine_profile, "machine_profile"))

    provider_items = declaration.get("provider")
    if not isinstance(provider_items, list):
        _fail("declaration.provider", "must be an array of tables")
    providers: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(provider_items):
        path = f"declaration.provider[{index}]"
        provider = _table(raw, path)
        _closed(provider, {"id", "kind", "implementation", "contract", "configuration"}, path)
        identity = _string(provider, "id", path)
        kind = _string(provider, "kind", path)
        implementation = _string(provider, "implementation", path)
        contract = _string(provider, "contract", path)
        configuration = _table(provider.get("configuration"), path + ".configuration")
        if identity in providers:
            _fail(path + ".id", f"duplicate provider identity: {identity}")
        if kind not in CONTRACTS:
            _fail(path + ".kind", f"unknown provider kind: {kind}")
        if contract != CONTRACTS[kind]:
            _fail(path + ".contract", f"must be {CONTRACTS[kind]} for {kind}")
        # provider-binding-configuration: committed, kind-specific provider semantics.
        if kind == "engine":
            _closed(configuration, {"build_command"}, path + ".configuration")
            resolved_configuration = {
                "build_command": copy.deepcopy(
                    _string_list(configuration, "build_command", path + ".configuration", nonempty=True)
                )
            }
        elif kind == "credential.github":
            _closed(configuration, {"source"}, path + ".configuration")
            source_path = path + ".configuration.source"
            source = resolve_machine_default(
                _string(configuration, "source", path + ".configuration"),
                machine_values["defaults"],
                source_path,
            )
            if source not in GITHUB_CREDENTIAL_SOURCES:
                _fail(source_path, "must be github-app or github-cli-user")
            resolved_configuration = {"source": source}
        else:
            _closed(configuration, set(), path + ".configuration")
            resolved_configuration = {}
        if ":" not in implementation:
            _fail(path + ".implementation", "must be <source lock id>:<relative executable entry point>")
        lock_ref, entry_point = implementation.split(":", 1)
        if lock_ref not in pins:
            _fail(path + ".implementation", f"references missing pin: {lock_ref}")
        if not entry_point or os.path.isabs(entry_point) or PurePosixPath(entry_point).is_absolute() or ".." in PurePosixPath(entry_point).parts:
            _fail(path + ".implementation", "entry point must be a safe relative path")
        providers[identity] = {"id": identity, "kind": kind, "implementation": implementation,
                               "contract": contract, "configuration": resolved_configuration}

    _resolve_provider_commands(providers, machine_values["tools"])

    deployments = declaration.get("deployment")
    if not isinstance(deployments, list) or not deployments:
        _fail("declaration.deployment", "must be a non-empty array of tables")
    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    resolved_deployments: list[dict[str, Any]] = []
    for index, raw in enumerate(deployments):
        path = f"declaration.deployment[{index}]"
        dep = _table(raw, path)
        # `github_write_enabled` is accepted and ignored, not honoured. Writing is unconditional,
        # so the field selects nothing — there is no second behaviour to keep. It stays in the
        # allowed set only because the schema and the declarations that feed it live in separate
        # repositories with no transaction between them: requiring its absence here would reject
        # every declaration until fkst-deployments merged, and requiring its presence was the
        # defect. It is removed once no declaration carries it.
        _closed(dep, {"id", "target_identity", "github_write_enabled", "claim_posture", "managed_bot_logins", "author_authorization", "github_devloop_profile", "sources", "package_sources", "engine_revision", "packages", "integration", "machine", "providers"}, path)
        identity = _string(dep, "id", path)
        target = _string(dep, "target_identity", path)
        claim_path = path + ".claim_posture"
        claim = _table(dep.get("claim_posture"), claim_path)
        _closed(claim, {"mode", "label_exclusive"}, claim_path)
        claim_mode = _string(claim, "mode", claim_path)
        if claim_mode not in {"assignee", "label"}:
            _fail(claim_path + ".mode", "must be assignee or label")
        claim_label_exclusive = claim.get("label_exclusive")
        if not isinstance(claim_label_exclusive, bool):
            _fail(claim_path + ".label_exclusive", "must be a boolean")
        managed_bot_logins: list[str] = []
        if "managed_bot_logins" in dep:
            managed_bot_logins = _platform_login_list(
                dep, "managed_bot_logins", path, nonempty=True
            )
            _require_no_domain_a_collapse(
                managed_bot_logins, path + ".managed_bot_logins"
            )
            _require_unique_logins(managed_bot_logins, path + ".managed_bot_logins")
        elif "github_devloop_profile" in dep:
            _fail(path + ".managed_bot_logins", "must be a non-empty string list")
        author_authorization_path = path + ".author_authorization"
        author_authorization = dep.get("author_authorization", {})
        author_authorization = _table(author_authorization, author_authorization_path)
        _closed(
            author_authorization,
            {"authorized_logins", "authorize_org_members", "authorize_repo_collaborators"},
            author_authorization_path,
        )
        authorized_logins = _platform_login_list(
            author_authorization, "authorized_logins", author_authorization_path, nonempty=False
        ) if "authorized_logins" in author_authorization else []
        _require_no_domain_a_collapse(
            authorized_logins,
            author_authorization_path + ".authorized_logins",
        )
        # Preserve the existing authorized-login uniqueness semantics; the
        # domain-A check above is an additional cross-domain invariant only.
        _require_unique(authorized_logins, author_authorization_path + ".authorized_logins")
        authorize_org_members = author_authorization.get("authorize_org_members", False)
        if not isinstance(authorize_org_members, bool):
            _fail(author_authorization_path + ".authorize_org_members", "must be a boolean")
        authorize_repo_collaborators = author_authorization.get("authorize_repo_collaborators", False)
        if not isinstance(authorize_repo_collaborators, bool):
            _fail(author_authorization_path + ".authorize_repo_collaborators", "must be a boolean")
        if identity in seen_ids:
            _fail(path + ".id", f"duplicate deployment identity: {identity}")
        if target in seen_targets:
            _fail(path + ".target_identity", f"duplicate target identity: {target}")
        seen_ids.add(identity)
        seen_targets.add(target)

        sources = _table(dep.get("sources"), path + ".sources")
        _closed(sources, {"target", "platform", "engine"}, path + ".sources")
        resolved_sources: dict[str, Any] = {}
        for role in ("target", "platform", "engine"):
            source = _table(sources.get(role), f"{path}.sources.{role}")
            _closed(source, {"lock_ref"}, f"{path}.sources.{role}")
            lock_ref = _string(source, "lock_ref", f"{path}.sources.{role}")
            if lock_ref not in pins:
                _fail(f"{path}.sources.{role}.lock_ref", f"references missing pin: {lock_ref}")
            if pins[lock_ref]["checkout_role"] != "deployment-operated":
                _fail(
                    f"{path}.sources.{role}.lock_ref",
                    f"references {pins[lock_ref]['checkout_role']} source; deployment sources must be deployment-operated",
                )
            resolved_sources[role] = {
                "lock_ref": lock_ref,
                "git": pins[lock_ref]["git"],
                "checkout_role": pins[lock_ref]["checkout_role"],
            }
            if "resolved" in pins[lock_ref]:
                resolved_sources[role]["resolved"] = copy.deepcopy(pins[lock_ref]["resolved"])

        # A deployment composes from its platform by default. Naming further package sources here
        # is what lets a target carry no packages of its own: the composition is described where
        # the deployment is declared, not inside the repository being operated. Each entry binds
        # one pinned source to one machine checkout and lists what that source supplies, which is
        # the same three facts the target-owned `[[external_sources]]` route carries.
        package_sources_path = path + ".package_sources"
        package_sources_declared = dep.get("package_sources", [])
        if not isinstance(package_sources_declared, list):
            _fail(package_sources_path, "must be an array of tables")
        resolved_package_sources: list[dict[str, Any]] = []
        for entry_index, entry in enumerate(package_sources_declared):
            entry_path = f"{package_sources_path}[{entry_index}]"
            entry_table = _table(entry, entry_path)
            _closed(entry_table, {"lock_ref", "checkout", "packages"}, entry_path)
            entry_ref = _string(entry_table, "lock_ref", entry_path)
            if entry_ref not in pins:
                _fail(entry_path + ".lock_ref", f"references missing pin: {entry_ref}")
            if pins[entry_ref]["checkout_role"] != "deployment-operated":
                _fail(
                    entry_path + ".lock_ref",
                    f"references {pins[entry_ref]['checkout_role']} source; deployment sources must be deployment-operated",
                )
            if any(entry_ref == source["lock_ref"] for source in resolved_sources.values()):
                _fail(entry_path + ".lock_ref", f"already declared as a deployment source: {entry_ref}")
            if any(entry_ref == existing["lock_ref"] for existing in resolved_package_sources):
                _fail(entry_path + ".lock_ref", f"duplicate package source: {entry_ref}")
            entry_checkout = _string(entry_table, "checkout", entry_path)
            _logical(entry_checkout, entry_path + ".checkout")
            if entry_checkout not in machine_values["roots"]:
                _fail(entry_path + ".checkout", f"unresolved logical roots reference: {entry_checkout}")
            entry_packages = _string_list(entry_table, "packages", entry_path, nonempty=True)
            for package_index, package_name in enumerate(entry_packages):
                _validate_package_name(
                    package_name, f"{entry_path}.packages[{package_index}]"
                )
            _require_unique(entry_packages, entry_path + ".packages")
            resolved_entry: dict[str, Any] = {
                "lock_ref": entry_ref,
                "git": pins[entry_ref]["git"],
                "checkout_role": pins[entry_ref]["checkout_role"],
                "checkout": copy.deepcopy(machine_values["roots"][entry_checkout]),
                "checkout_reference": entry_checkout,
                "packages": entry_packages,
            }
            if "resolved" in pins[entry_ref]:
                resolved_entry["resolved"] = copy.deepcopy(pins[entry_ref]["resolved"])
            resolved_package_sources.append(resolved_entry)

        engine_revision_path = path + ".engine_revision"
        engine_revision = _table(dep.get("engine_revision"), engine_revision_path)
        _closed(engine_revision, {"path"}, engine_revision_path)
        derivation_path = _string(engine_revision, "path", engine_revision_path)
        try:
            validate_derivation_path(derivation_path)
        except RevisionDerivationError as exc:
            _fail(engine_revision_path + ".path", str(exc))

        packages = _table(dep.get("packages"), path + ".packages")
        _closed(packages, {"platform", "host"}, path + ".packages")
        # platform is optional: a declaration that omits it leaves composition to the target
        # repository's own manifest, which is the fallback the operator takes. A declaration
        # that carries it owns the composition outright and the target need not describe it.
        resolved_packages = {"platform": [], "host": []}
        if "platform" in packages:
            resolved_packages["platform"] = _string_list(packages, "platform", path + ".packages", nonempty=True)
        if "host" in packages:
            resolved_packages["host"] = _string_list(packages, "host", path + ".packages", nonempty=False)
        # These names become engine package-root basenames and cross a whitespace-separated
        # transport into the launch contract, so enforce the engine's complete name grammar here.
        for field_name in ("platform", "host"):
            for package_index, package_name in enumerate(resolved_packages[field_name]):
                _validate_package_name(
                    package_name, f"{path}.packages.{field_name}[{package_index}]"
                )
        _require_unique(resolved_packages["platform"], path + ".packages.platform")
        _require_unique(resolved_packages["host"], path + ".packages.host")
        # The engine rejects duplicate package-root basenames. Reject the same composition here so
        # a declaration cannot validate successfully and then abort at the launch boundary.
        _require_unique(
            resolved_packages["platform"] + resolved_packages["host"]
            + [name for entry in resolved_package_sources for name in entry["packages"]],
            path + ".packages",
        )

        integration = _table(dep.get("integration"), path + ".integration")
        _closed(integration, {"upstream_branch", "integration_branch", "rollup_merge"}, path + ".integration")
        resolved_integration = {name: _string(integration, name, path + ".integration") for name in ("upstream_branch", "integration_branch", "rollup_merge")}
        resolved_integration["integration_branch"] = resolve_machine_default(
            resolved_integration["integration_branch"], machine_values["defaults"],
            path + ".integration.integration_branch",
        )

        bindings = _table(dep.get("providers"), path + ".providers")
        _closed(bindings, set(PROVIDER_FIELDS), path + ".providers")
        resolved_bindings: dict[str, Any] = {}
        selected: set[str] = set()
        for field, required_kind in PROVIDER_FIELDS.items():
            binding_id = _string(bindings, field, path + ".providers")
            if binding_id in selected:
                _fail(path + ".providers." + field, f"duplicate provider binding: {binding_id}")
            selected.add(binding_id)
            provider = providers.get(binding_id)
            if provider is None:
                _fail(path + ".providers." + field, f"missing provider binding: {binding_id}")
            if provider["kind"] != required_kind:
                _fail(path + ".providers." + field, f"binding kind must be {required_kind}")
            resolved_bindings[field] = copy.deepcopy(provider)

        resolved: dict[str, Any] = {
            "id": identity,
            "target_identity": target,
            "claim_posture": {"mode": claim_mode, "label_exclusive": claim_label_exclusive},
            "managed_bot_logins": copy.deepcopy(managed_bot_logins),
            "author_authorization": {
                "authorized_logins": authorized_logins,
                "authorize_org_members": authorize_org_members,
                "authorize_repo_collaborators": authorize_repo_collaborators,
            },
        }
        profile_block = dep.get("github_devloop_profile")
        if profile_block is not None:
            profile_path = path + ".github_devloop_profile"
            profile_table = _table(profile_block, profile_path)
            _closed(profile_table, {"version", "id", "data", "producer_binding"}, profile_path)
            producer = _string(profile_table, "producer_binding", profile_path)
            if producer != bindings["board_github_control"]:
                _fail(profile_path + ".producer_binding", "must equal the deployment board_github_control binding")
            resolved["github_devloop_profile"] = {
                "version": _string(profile_table, "version", profile_path),
                "id": _string(profile_table, "id", profile_path),
                "data": copy.deepcopy(_table(profile_table.get("data"), profile_path + ".data")),
                "producer_binding": producer,
            }
        resolved.update({"sources": resolved_sources,
                         "package_sources": resolved_package_sources,
                         "engine_revision": {"path": derivation_path},
                         "packages": resolved_packages, "integration": resolved_integration,
                         "machine": _resolve_machine(_table(dep.get("machine"), path + ".machine"), machine_values,
                                                     path + ".machine", profile_present=profile_block is not None),
                         "providers": resolved_bindings})
        bot_login = resolved["machine"].get("bot_login")
        # This membership rule is selected, not derived from platform consumer
        # semantics: both consumers tolerate a roster that omits this machine.
        # A committed cross-machine roster is treated as the complete fleet so a
        # machine cannot run against a declaration that excludes itself. The
        # explicit cost of that fail-closed choice is rejecting peers-only rosters.
        if bot_login is not None and normalized_login(bot_login) not in {
            normalized_login(login) for login in managed_bot_logins
        }:
            _fail(
                path + ".machine.bot_login",
                "resolved bot login must belong to deployment.managed_bot_logins",
            )
        if bot_login is not None:
            _require_actor_no_domain_a_collapse(
                bot_login, managed_bot_logins, path + ".machine.bot_login"
            )
        if require_materialized_paths:
            _validate_resolved_paths(resolved, path, pins)
        resolved_deployments.append(resolved)
    return {
        "schema": SCHEMA_ID,
        "cadence_enabled": cadence_enabled,
        "cadence_interval_seconds": cadence_interval_seconds,
        "guard_restart_attempt_limit": guard_restart_attempt_limit,
        "deployment": resolved_deployments,
    }


def load_and_resolve(
    declaration_path: str | Path,
    machine_profile_path: str | Path,
    lock_path: str | Path,
    *,
    require_materialized_paths: bool = True,
) -> dict[str, Any]:
    """Parse three TOML inputs, validate them completely, then return resolved data."""
    documents = []
    for label, path in (("declaration", declaration_path), ("machine profile", machine_profile_path), ("lock", lock_path)):
        try:
            with Path(path).open("rb") as handle:
                documents.append(tomllib.load(handle))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValidationError(f"{label}: cannot load {path}: {exc}") from exc
    return validate_and_resolve(
        *documents, require_materialized_paths=require_materialized_paths
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("declaration")
    parser.add_argument("machine_profile")
    parser.add_argument("lock")
    args = parser.parse_args(argv)
    try:
        resolved = load_and_resolve(args.declaration, args.machine_profile, args.lock)
    except ValidationError as exc:
        print(f"deployment preflight failed: {exc}", file=sys.stderr)
        return 2
    json.dump(resolved, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
