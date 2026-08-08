#!/usr/bin/env python3
"""Generate all machine-local artifacts for one deployment repository."""

from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema.validator import SCHEMA_ID, ValidationError, load_and_resolve
from bootstrap.canonical_tree import canonical_tree_sha256


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "watch" / "com.fkst.cadence.plist.template"


def _run_git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def _verified_checkout(root: Path, revision: str, tree: str) -> bool:
    try:
        if _run_git(root, "rev-parse", "HEAD") != revision:
            return False
        if canonical_tree_sha256(root, revision) != tree:
            return False
        # The canonical hasher proves the commit; this proves the materialised
        # tracked files still represent it. Build outputs are intentionally ignored.
        return not _run_git(root, "status", "--porcelain", "--untracked-files=no")
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
        return False


def _source_candidates(home: Path, source_url: str) -> list[Path]:
    name = Path(source_url.removesuffix("/")).name.removesuffix(".git")
    return [home / name, home / ".cache" / "fkst" / name]


def _materialise_checkout(
    destination: Path, source_url: str, revision: str, tree: str, home: Path
) -> None:
    parsed = urlsplit(source_url)
    if parsed.scheme in {"http", "https"} and (
        parsed.username is not None or parsed.password is not None
    ):
        raise ValueError(f"checkout {destination.name} has a credential-bearing source URL")
    if _verified_checkout(destination, revision, tree):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        reusable = next(
            (candidate for candidate in _source_candidates(home, source_url)
             if candidate.resolve() != destination.resolve()
             and _verified_checkout(candidate, revision, tree)),
            None,
        )
        clone_source = reusable if reusable is not None else source_url
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", str(clone_source), str(temporary)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        subprocess.run(
            ["git", "-C", str(temporary), "checkout", "--quiet", "--detach", revision],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        if not _verified_checkout(temporary, revision, tree):
            raise ValueError(
                f"checkout {destination.name} does not match pin {revision} / {tree}"
            )
        if destination.exists() or destination.is_symlink():
            shutil.rmtree(destination) if destination.is_dir() and not destination.is_symlink() else destination.unlink()
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _engine_product(checkout: Path, command: list[str]) -> Path:
    if command and Path(command[0]).name == "cargo" and "-p" in command:
        index = command.index("-p")
        if index + 1 < len(command):
            return checkout / "target" / "debug" / command[index + 1]
    raise ValueError(
        "engine build output is not reproducible from build_command; "
        "cargo builds must declare -p <binary-package>"
    )


def _materialise_engine(
    checkout: Path, binary: Path, command: list[str], revision: str, tree: str, state: Path
) -> None:
    fingerprint = hashlib.sha256(json.dumps(
        {"revision": revision, "tree_sha256": tree, "build_command": command},
        sort_keys=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    marker = state / "engine-build.json"
    try:
        settled = json.loads(marker.read_text(encoding="ascii")).get("fingerprint") == fingerprint
    except (OSError, json.JSONDecodeError, AttributeError):
        settled = False
    if settled and binary.is_file() and os.access(binary, os.X_OK):
        return
    result = subprocess.run(command, cwd=checkout, check=False)
    if result.returncode != 0:
        raise ValueError(f"engine build failed with exit code {result.returncode}: {command[0]}")
    product = _engine_product(checkout, command)
    if not product.is_file() or not os.access(product, os.X_OK):
        raise ValueError(f"engine build did not produce executable: {product}")
    binary.parent.mkdir(parents=True, exist_ok=True)
    pointer = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
    pointer.unlink(missing_ok=True)
    pointer.symlink_to(product)
    os.replace(pointer, binary)
    state.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"fingerprint": fingerprint}, sort_keys=True) + "\n", encoding="ascii")
    os.replace(temporary, marker)


def _hydrate(
    declarations: list[tuple[Path, dict[str, Any]]], lock_path: Path, home: Path
) -> None:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    pins = {entry["id"]: entry for entry in lock.get("external_source", [])}
    base = home / ".fkst" / "machine"
    checkout_specs: dict[str, tuple[str, str, str]] = {}
    engine_specs: dict[str, tuple[str, list[str]]] = {}
    preserved_roots: set[str] = set()
    for declaration_path, declaration in declarations:
        providers = {provider["id"]: provider for provider in declaration["provider"]}
        for index, deployment in enumerate(declaration["deployment"]):
            machine = deployment["machine"]
            for role in ("target", "platform", "engine"):
                logical = machine[f"{role}_checkout"]
                source_id = deployment["sources"][role]["lock_ref"]
                try:
                    pin = pins[source_id]
                    spec = (pin["git"], pin["resolved"]["rev"], pin["resolved"]["tree_sha256"])
                except (KeyError, TypeError) as exc:
                    raise ValueError(f"{declaration_path} deployment[{index}] source {source_id} has no complete pin") from exc
                if logical in checkout_specs and checkout_specs[logical] != spec:
                    raise ValueError(f"checkout root {logical} is assigned conflicting pins")
                checkout_specs[logical] = spec
            for field in ("durable", "runtime", "logs", "rate_pool"):
                preserved_roots.add(machine[field])
            engine_provider = providers[deployment["providers"]["engine"]]
            engine_spec = (
                machine["engine_checkout"], engine_provider["configuration"]["build_command"]
            )
            binary_name = machine["engine_binary"]
            if binary_name in engine_specs and engine_specs[binary_name] != engine_spec:
                raise ValueError(f"engine binary {binary_name} is assigned conflicting build inputs")
            engine_specs[binary_name] = engine_spec
    for logical, (url, revision, tree) in checkout_specs.items():
        _materialise_checkout(base / "roots" / logical, url, revision, tree, home)
    # These roots contain accumulated or runtime state. Generation creates them,
    # but never removes or replaces their contents.
    for logical in preserved_roots:
        (base / "roots" / logical).mkdir(parents=True, exist_ok=True)
    for binary_name, (checkout_name, command) in engine_specs.items():
        _, revision, tree = checkout_specs[checkout_name]
        _materialise_engine(
            base / "roots" / checkout_name, base / "bin" / binary_name,
            command, revision, tree, base / "state",
        )


def _load_declarations(repository: Path) -> list[tuple[Path, dict[str, Any]]]:
    found: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(repository.rglob("*.toml")):
        if ".fkst" in path.relative_to(repository).parts:
            continue
        try:
            with path.open("rb") as stream:
                document = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read declaration candidate {path}: {exc}") from exc
        if document.get("schema") == SCHEMA_ID:
            found.append((path, document))
    if not found:
        raise ValueError(f"no {SCHEMA_ID} declarations found below {repository}")
    return found


def _quoted(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=True)


def _profile_text(declarations: list[tuple[Path, dict[str, Any]]], home: Path) -> str:
    base = home / ".fkst" / "machine"
    roots: dict[str, str] = {}
    binaries: dict[str, str] = {}
    credentials: dict[str, str] = {}
    sets: dict[str, list[str]] = {}

    for declaration_path, declaration in declarations:
        for index, deployment in enumerate(declaration["deployment"]):
            logins = deployment.get("managed_bot_logins")
            if not isinstance(logins, list) or len(logins) != 1 or not isinstance(logins[0], str) or not logins[0]:
                raise ValueError(
                    f"declaration {declaration_path} deployment[{index}].managed_bot_logins "
                    "must declare exactly one bot login"
                )
            login = logins[0]
            machine = deployment["machine"]
            for field in (
                "target_checkout", "platform_checkout", "engine_checkout",
                "durable", "runtime", "logs", "rate_pool",
            ):
                logical = machine.get(field)
                if logical:
                    roots.setdefault(logical, str(base / "roots" / logical))
            binaries.setdefault(
                machine["engine_binary"], str(base / "bin" / machine["engine_binary"])
            )
            credential_name = machine["bot_login"]
            if credential_name in credentials and credentials[credential_name] != login:
                raise ValueError(f"conflicting declarations for bot login {credential_name}")
            credentials[credential_name] = login
            set_name = machine["managed_bot_set"]
            value = deployment["managed_bot_logins"]
            if set_name in sets and sets[set_name] != value:
                raise ValueError(f"conflicting declarations for managed bot set {set_name}")
            sets[set_name] = value

    lines = ['schema = "fkst.ops.machine-profile.v1"', ""]
    for heading, values in (
        ("roots", roots), ("binaries", binaries), ("credentials", credentials)
    ):
        lines.append(f"[{heading}]")
        lines.extend(f"{_quoted(key)} = {_quoted(value)}" for key, value in sorted(values.items()))
        lines.append("")
    lines.append("[sets]")
    for key, values in sorted(sets.items()):
        lines.append(f"{_quoted(key)} = [{', '.join(_quoted(item) for item in values)}]")
    lines.extend(("", "[defaults]", ""))
    return "\n".join(lines)


def _plist_text(
    repository: Path, profile: Path, home: Path, interval: int
) -> str:
    values = {
        "__PYTHON3__": sys.executable,
        "__FKST_OPS_CHECKOUT__": str(ROOT),
        "__DEPLOYMENT_REPOSITORY__": str(repository),
        "__MACHINE_PROFILE__": str(profile),
        "__LEDGER__": str(home / ".fkst" / "watch" / "cadence.jsonl"),
        "__INTERVAL_SECONDS__": str(interval),
        "__STANDARD_OUT_LOG__": str(home / ".fkst" / "watch" / "cadence.stdout.log"),
        "__STANDARD_ERROR_LOG__": str(home / ".fkst" / "watch" / "cadence.stderr.log"),
    }
    rendered = TEMPLATE.read_text(encoding="ascii")
    for marker, value in values.items():
        rendered = rendered.replace(marker, escape(value))
    if "__" in rendered:
        raise ValueError("cadence plist template contains an unresolved placeholder")
    plistlib.loads(rendered.encode("utf-8"))
    return rendered


def _launchctl(home: Path, launch_agent: Path, enabled: bool) -> bool:
    executable = os.environ.get("FKST_LAUNCHCTL", "/bin/launchctl")
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/com.fkst.cadence"

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [executable, *arguments], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    if run("print", service).returncode == 0:
        result = run("bootout", service)
        if result.returncode != 0:
            raise ValueError(f"cannot unload cadence schedule: {result.stderr.strip()}")
    if enabled:
        result = run("enable", service)
        if result.returncode == 0:
            result = run("bootstrap", domain, str(launch_agent))
    else:
        result = run("disable", service)
    if result.returncode != 0:
        action = "activate" if enabled else "deactivate"
        raise ValueError(f"cannot {action} cadence schedule: {result.stderr.strip()}")
    live = run("print", service).returncode == 0
    if live != enabled:
        raise ValueError(
            f"cadence schedule reconciliation failed: requested enabled={enabled}, live={live}"
        )
    return live


def generate(repository: Path, home: Path) -> tuple[Path, Path, bool, int]:
    repository = repository.resolve()
    home = home.resolve()
    declarations = _load_declarations(repository)
    intervals = {document.get("cadence_interval_seconds") for _, document in declarations}
    if len(intervals) != 1:
        raise ValueError("all deployment declarations must use one cadence_interval_seconds")
    interval = intervals.pop()
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        raise ValueError("cadence_interval_seconds must be a positive integer")
    enablements = {document.get("cadence_enabled") for _, document in declarations}
    if len(enablements) != 1:
        raise ValueError("all deployment declarations must use one cadence_enabled")
    enabled = enablements.pop()
    if not isinstance(enabled, bool):
        raise ValueError("cadence_enabled must be a boolean")

    profile = repository / ".fkst" / "machine-profile.toml"
    launch_agent = home / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    profile.parent.mkdir(parents=True, exist_ok=True)
    (home / ".fkst" / "watch").mkdir(parents=True, exist_ok=True)
    launch_agent.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(_profile_text(declarations, home), encoding="ascii")

    lock = repository / "fkst.lock"
    _hydrate(declarations, lock, home)
    for declaration_path, _ in declarations:
        load_and_resolve(declaration_path, profile, lock)

    launch_agent.write_text(
        _plist_text(repository, profile, home, interval), encoding="utf-8"
    )
    live = _launchctl(home, launch_agent, enabled)
    return profile, launch_agent, live, interval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_repository", type=Path)
    args = parser.parse_args(argv)
    try:
        profile, launch_agent, live, interval = generate(args.deployment_repository, Path.home())
    except (OSError, ValueError, ValidationError) as exc:
        print(f"artifact generation failed: {exc}", file=sys.stderr)
        return 2
    print(profile)
    print(launch_agent)
    print(f"cadence_schedule={'enabled' if live else 'disabled'} live={'yes' if live else 'no'} interval_seconds={interval}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
