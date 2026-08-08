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
from typing import Any, NoReturn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema.provider_surface import MECHANISM_SOURCE_ID, PUBLISHED_PROVIDER_ENTRY_POINTS


SCHEMA_ID = "fkst.ops.deployment.v1"
MACHINE_SCHEMA_ID = "fkst.ops.machine-profile.v1"
CONTRACTS = {
    "engine": "fkst.ops.engine.v1",
    "board.engine-durable": "fkst.ops.board.engine-durable.v1",
    "board.github-control": "fkst.ops.board.github-control.v1",
}
PROVIDER_FIELDS = {
    "engine": "engine",
    "board_engine_durable": "board.engine-durable",
    "board_github_control": "board.github-control",
}
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
    "managed_bot_set": "sets",
}
PROFILE_MACHINE_FIELDS = {"rate_pool", "bot_login", "managed_bot_set"}
_SHA = re.compile(r"^[0-9a-f]{40}$")
_TREE_SHA = re.compile(r"^sha256-[0-9a-f]{64}$")


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


def _logical(value: str, path: str) -> None:
    if os.path.isabs(value) or PurePosixPath(value).is_absolute():
        _fail(path, "absolute machine value is forbidden; use a logical name")
    if "/" in value or "\\" in value:
        _fail(path, "must be a logical name, not a path")


def _validate_lock(lock: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _closed(lock, {"external_source"}, "lock")
    entries = lock.get("external_source")
    if not isinstance(entries, list) or not entries:
        _fail("lock.external_source", "must be a non-empty array of tables")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(entries):
        path = f"lock.external_source[{index}]"
        entry = _table(raw, path)
        _closed(entry, {"id", "git", "intent", "resolved", "libraries"}, path)
        identity = _string(entry, "id", path)
        _string(entry, "git", path)
        resolved = _table(entry.get("resolved"), f"{path}.resolved")
        _closed(resolved, {"rev", "tree_sha256"}, f"{path}.resolved")
        rev = _string(resolved, "rev", f"{path}.resolved")
        tree = _string(resolved, "tree_sha256", f"{path}.resolved")
        if not _SHA.fullmatch(rev):
            _fail(f"{path}.resolved.rev", "must be a full lowercase Git SHA")
        if not _TREE_SHA.fullmatch(tree):
            _fail(f"{path}.resolved.tree_sha256", "must be a canonical SHA-256 pin")
        if identity in result:
            _fail(path + ".id", f"duplicate lock identity: {identity}")
        result[identity] = entry
    return result


def _validate_machine_profile(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    allowed = {"schema", "roots", "binaries", "credentials", "sets", "defaults"}
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
            if kind in {"roots", "binaries"} and not os.path.isabs(value):
                _fail(f"machine_profile.{kind}.{name}", "must be an absolute path")
        result[kind] = values
    return result


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


def _validate_resolved_paths(resolved: dict[str, Any], path: str, pins: dict[str, dict[str, Any]]) -> None:
    machine = resolved["machine"]
    checkouts = {
        role: _require_directory(machine[f"{role}_checkout"], f"{path}.machine.{role}_checkout")
        for role in ("target", "platform", "engine")
    }
    _require_directory(machine["durable"], path + ".machine.durable")

    for package in resolved["packages"]["platform"]:
        _require_directory(str(checkouts["platform"] / "packages" / package), path + f".packages.platform[{package}]")
    for package in resolved["packages"]["host"]:
        _require_directory(str(checkouts["target"] / ".fkst" / "local-packages" / package), path + f".packages.host[{package}]")

    source_roots: dict[str, Path] = {}
    for role, source in resolved["sources"].items():
        lock_ref = source["lock_ref"]
        root = checkouts[role]
        if lock_ref in source_roots and source_roots[lock_ref].resolve() != root.resolve():
            _fail(path + f".sources.{role}.lock_ref", f"lock reference {lock_ref} resolves to multiple checkouts")
        source_roots[lock_ref] = root
    # provider-mechanism-source-root: bootstrap/run.sh verifies this checkout
    # against this lock entry before handing control to the validator.
    if MECHANISM_SOURCE_ID in pins:
        source_roots[MECHANISM_SOURCE_ID] = Path(__file__).resolve().parents[1]
    for field, provider in resolved["providers"].items():
        lock_ref, relative = provider["implementation"].split(":", 1)
        root = source_roots.get(lock_ref)
        if root is None:
            _fail(path + f".providers.{field}", f"provider source is not bound to a deployment or mechanism source: {lock_ref}")
        if lock_ref == MECHANISM_SOURCE_ID:
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


def validate_and_resolve(declaration: dict[str, Any], machine_profile: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    """Validate all inputs and return a newly allocated resolved declaration."""
    declaration = _table(declaration, "declaration")
    _closed(declaration, {"schema", "deployment", "provider"}, "declaration")
    if _string(declaration, "schema", "declaration") != SCHEMA_ID:
        _fail("declaration.schema", f"must be {SCHEMA_ID}")
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

    deployments = declaration.get("deployment")
    if not isinstance(deployments, list) or not deployments:
        _fail("declaration.deployment", "must be a non-empty array of tables")
    seen_ids: set[str] = set()
    seen_targets: set[str] = set()
    resolved_deployments: list[dict[str, Any]] = []
    for index, raw in enumerate(deployments):
        path = f"declaration.deployment[{index}]"
        dep = _table(raw, path)
        _closed(dep, {"id", "target_identity", "github_devloop_profile", "sources", "packages", "integration", "machine", "providers"}, path)
        identity = _string(dep, "id", path)
        target = _string(dep, "target_identity", path)
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
            resolved_sources[role] = {
                "lock_ref": lock_ref,
                "git": pins[lock_ref]["git"],
                "pin": copy.deepcopy(pins[lock_ref]["resolved"]),
            }

        packages = _table(dep.get("packages"), path + ".packages")
        _closed(packages, {"platform", "host"}, path + ".packages")
        resolved_packages = {"platform": _string_list(packages, "platform", path + ".packages", nonempty=True), "host": []}
        if "host" in packages:
            resolved_packages["host"] = _string_list(packages, "host", path + ".packages", nonempty=False)
        _require_unique(resolved_packages["platform"], path + ".packages.platform")
        _require_unique(resolved_packages["host"], path + ".packages.host")

        integration = _table(dep.get("integration"), path + ".integration")
        _closed(integration, {"upstream_branch", "integration_branch", "rollup_merge"}, path + ".integration")
        resolved_integration = {name: _string(integration, name, path + ".integration") for name in ("upstream_branch", "integration_branch", "rollup_merge")}
        branch = resolved_integration["integration_branch"]
        if branch.startswith("machine:"):
            logical = branch.removeprefix("machine:")
            _logical(logical, path + ".integration.integration_branch")
            if logical not in machine_values["defaults"]:
                _fail(path + ".integration.integration_branch", f"unresolved logical defaults reference: {logical}")
            resolved_integration["integration_branch"] = machine_values["defaults"][logical]

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

        resolved: dict[str, Any] = {"id": identity, "target_identity": target}
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
        resolved.update({"sources": resolved_sources, "packages": resolved_packages, "integration": resolved_integration,
                         "machine": _resolve_machine(_table(dep.get("machine"), path + ".machine"), machine_values,
                                                     path + ".machine", profile_present=profile_block is not None),
                         "providers": resolved_bindings})
        _validate_resolved_paths(resolved, path, pins)
        resolved_deployments.append(resolved)
    return {"schema": SCHEMA_ID, "deployment": resolved_deployments}


def load_and_resolve(declaration_path: str | Path, machine_profile_path: str | Path, lock_path: str | Path) -> dict[str, Any]:
    """Parse three TOML inputs, validate them completely, then return resolved data."""
    documents = []
    for label, path in (("declaration", declaration_path), ("machine profile", machine_profile_path), ("lock", lock_path)):
        try:
            with Path(path).open("rb") as handle:
                documents.append(tomllib.load(handle))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValidationError(f"{label}: cannot load {path}: {exc}") from exc
    return validate_and_resolve(*documents)


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
