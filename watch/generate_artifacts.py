#!/usr/bin/env python3
"""Generate all machine-local artifacts for one deployment repository."""

from __future__ import annotations

import argparse
import fcntl
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
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema.validator import SCHEMA_ID, ValidationError, load_and_resolve
from bootstrap.canonical_tree import canonical_tree_sha256


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "watch" / "com.fkst.cadence.plist.template"
DECLARATION_SET_NAME = "deployment-set.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_repository_file(repository: Path, relative: str, label: str) -> Path:
    item = Path(relative)
    if item.is_absolute() or ".." in item.parts:
        raise ValueError(f"forbidden {label} path: {relative}")
    target = (repository / item).resolve()
    try:
        canonical_relative = target.relative_to(repository)
    except ValueError as exc:
        raise ValueError(f"{label} escapes deployment repository: {relative}") from exc
    if "tests" in canonical_relative.parts or ".fkst" in canonical_relative.parts:
        raise ValueError(f"forbidden canonical {label} target: {relative} -> {target}")
    if not target.is_file():
        raise ValueError(f"enumerated {label} does not exist: {relative}")
    return target


def _run_git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def _verified_checkout(root: Path, revision: str, tree: str, branch: str | None = None) -> bool:
    try:
        if _run_git(root, "rev-parse", "HEAD") != revision:
            return False
        if canonical_tree_sha256(root, revision) != tree:
            return False
        if branch is not None and _run_git(root, "branch", "--show-current") != branch:
            return False
        # The canonical hasher proves the commit; this proves the materialised
        # tracked files still represent it. Build outputs are intentionally ignored.
        return not _run_git(root, "status", "--porcelain", "--untracked-files=no")
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
        return False


def _verified_deployment_checkout(
    root: Path, revision: str, tree: str, branch: str
) -> bool:
    try:
        if _run_git(root, "branch", "--show-current") != branch:
            return False
        remote_branch = f"refs/remotes/origin/{branch}"
        if _run_git(root, "rev-parse", "--verify", remote_branch) == "":
            return False
        if canonical_tree_sha256(root, revision) != tree:
            return False
        ancestor = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", revision, remote_branch],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if ancestor.returncode != 0:
            return False
        return not _run_git(root, "status", "--porcelain", "--untracked-files=no")
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError):
        return False


def _source_candidates(home: Path, source_url: str) -> list[Path]:
    name = Path(source_url.removesuffix("/")).name.removesuffix(".git")
    return [home / name, home / ".cache" / "fkst" / name]


def _materialise_checkout(
    destination: Path, source_url: str, revision: str, tree: str, home: Path,
    checkout_role: str, branch: str | None = None,
) -> None:
    parsed = urlsplit(source_url)
    if parsed.scheme in {"http", "https"} and (
        parsed.username is not None or parsed.password is not None
    ):
        raise ValueError(f"checkout {destination.name} has a credential-bearing source URL")
    if checkout_role not in {"deployment-operated", "mechanism"}:
        raise ValueError(f"checkout {destination.name} has unknown role {checkout_role}")
    if checkout_role == "deployment-operated" and not branch:
        raise ValueError(f"deployment-operated checkout {destination.name} needs an integration branch")
    if checkout_role == "mechanism" and branch is not None:
        raise ValueError(f"mechanism checkout {destination.name} cannot declare a branch")
    destination_verified = (
        _verified_deployment_checkout(destination, revision, tree, branch)
        if checkout_role == "deployment-operated"
        else _verified_checkout(destination, revision, tree)
    )
    if destination_verified:
        return
    if destination.exists() or destination.is_symlink():
        raise ValueError(
            f"checkout {destination} does not match its declared reproducible state; "
            "refusing to replace existing work"
        )
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
        checkout_arguments = (
            ["checkout", "--quiet", "--detach", revision]
            if checkout_role == "mechanism"
            else ["checkout", "--quiet", "-B", branch, revision]
        )
        subprocess.run(
            ["git", "-C", str(temporary), *checkout_arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        temporary_verified = (
            _verified_deployment_checkout(temporary, revision, tree, branch)
            if checkout_role == "deployment-operated"
            else _verified_checkout(temporary, revision, tree)
        )
        if not temporary_verified:
            raise ValueError(
                f"checkout {destination.name} does not match pin {revision} / {tree} "
                f"or remote integration branch {branch} is missing"
            )
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
    declarations: list[tuple[Path, dict[str, Any]]], lock_path: Path, machine_root: Path
) -> None:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    pins = {entry["id"]: entry for entry in lock.get("external_source", [])}
    base = machine_root
    checkout_specs: dict[str, tuple[str, str, str, str, str]] = {}
    engine_specs: dict[str, tuple[str, list[str]]] = {}
    preserved_roots: set[str] = set()
    for declaration_path, declaration in declarations:
        providers = {provider["id"]: provider for provider in declaration["provider"]}
        for index, deployment in enumerate(declaration["deployment"]):
            machine = deployment["machine"]
            branch = deployment["integration"]["integration_branch"]
            for role in ("target", "platform", "engine"):
                logical = machine[f"{role}_checkout"]
                source_id = deployment["sources"][role]["lock_ref"]
                try:
                    pin = pins[source_id]
                    spec = (
                        pin["git"], pin["resolved"]["rev"], pin["resolved"]["tree_sha256"],
                        pin["checkout_role"], branch,
                    )
                except (KeyError, TypeError) as exc:
                    raise ValueError(f"{declaration_path} deployment[{index}] source {source_id} has no complete pin") from exc
                if logical in checkout_specs and checkout_specs[logical] != spec:
                    raise ValueError(f"checkout root {logical} is assigned conflicting pins, roles, or branches")
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
    for logical, (url, revision, tree, checkout_role, branch) in checkout_specs.items():
        _materialise_checkout(
            base / "roots" / logical, url, revision, tree, machine_root, checkout_role, branch
        )
    # These roots contain accumulated or runtime state. Generation creates them,
    # but never removes or replaces their contents.
    for logical in preserved_roots:
        (base / "roots" / logical).mkdir(parents=True, exist_ok=True)
    for binary_name, (checkout_name, command) in engine_specs.items():
        _, revision, tree, _, _ = checkout_specs[checkout_name]
        _materialise_engine(
            base / "roots" / checkout_name, base / "bin" / binary_name,
            command, revision, tree, base / "state",
        )


def _load_declarations(
    repository: Path,
) -> tuple[list[tuple[Path, dict[str, Any]]], Path]:
    parts = repository.resolve().parts
    if "tests" in parts or ".fkst" in parts:
        raise ValueError(f"deployment repository is forbidden control material: {repository}")
    declaration_set = _canonical_repository_file(
        repository, DECLARATION_SET_NAME, "input set"
    )
    try:
        document = json.loads(declaration_set.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read explicit declaration set {declaration_set}: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "declarations"}:
        raise ValueError("explicit declaration set has unknown or missing fields")
    if document["schema"] != "fkst.ops.declaration-input.v1":
        raise ValueError("explicit declaration set has unsupported schema")
    items = document["declarations"]
    if not isinstance(items, list) or not items or any(not isinstance(item, str) or not item for item in items):
        raise ValueError("explicit declaration set must enumerate non-empty relative paths")
    if len(set(items)) != len(items):
        raise ValueError("explicit declaration set contains duplicates")
    found: list[tuple[Path, dict[str, Any]]] = []
    for item in items:
        path = _canonical_repository_file(repository, item, "declaration")
        try:
            with path.open("rb") as stream:
                declaration = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read enumerated declaration {path}: {exc}") from exc
        if declaration.get("schema") != SCHEMA_ID:
            raise ValueError(f"enumerated declaration has wrong schema: {item}")
        found.append((path, declaration))
    return found, declaration_set


def _quoted(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=True)


def _profile_text(declarations: list[tuple[Path, dict[str, Any]]], machine_root: Path) -> str:
    base = machine_root
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
    repository: Path, profile: Path, manifest: Path, machine_root: Path, interval: int
) -> str:
    values = {
        "__PYTHON3__": sys.executable,
        "__FKST_OPS_CHECKOUT__": str(ROOT),
        "__DEPLOYMENT_REPOSITORY__": str(repository),
        "__MACHINE_PROFILE__": str(profile),
        "__DECLARATION_MANIFEST__": str(manifest),
        "__LEDGER__": str(machine_root / "watch" / "cadence.jsonl"),
        "__INTERVAL_SECONDS__": str(interval),
        "__STANDARD_OUT_LOG__": str(machine_root / "watch" / "cadence.stdout.log"),
        "__STANDARD_ERROR_LOG__": str(machine_root / "watch" / "cadence.stderr.log"),
    }
    rendered = TEMPLATE.read_text(encoding="ascii")
    for marker, value in values.items():
        rendered = rendered.replace(marker, escape(value))
    if "__" in rendered:
        raise ValueError("cadence plist template contains an unresolved placeholder")
    plistlib.loads(rendered.encode("utf-8"))
    return rendered


@dataclass(frozen=True)
class ScheduleState:
    live: bool
    source: Path | None


def _launchctl(launch_agent: Path, enabled: bool) -> bool:
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


def _schedule_state() -> ScheduleState:
    executable = os.environ.get("FKST_LAUNCHCTL", "/bin/launchctl")
    service = f"gui/{os.getuid()}/com.fkst.cadence"
    result = subprocess.run(
        [executable, "print", service], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        return ScheduleState(False, None)
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(" = ")
        if separator and key == "path" and value:
            return ScheduleState(True, Path(value).resolve(strict=False))
    raise ValueError("cannot identify source path of live cadence schedule")


def _publication_checkpoint(_point: str) -> None:
    """Test seam for abrupt-death verification; production publication is uninterrupted."""


def _relative_symlink(destination: Path, target: Path) -> None:
    candidate = destination.with_name(f".{destination.name}.{os.getpid()}.link")
    candidate.unlink(missing_ok=True)
    candidate.symlink_to(os.path.relpath(target, destination.parent))
    os.replace(candidate, destination)


def _prepare_control_lineage(staged: dict[Path, Path], control: Path) -> str | None:
    current = control / "current"
    generations = control / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    if current.is_symlink():
        previous = os.readlink(current)
    elif current.exists():
        raise ValueError(f"control generation selector is not a symlink: {current}")
    elif any(destination.exists() or destination.is_symlink() for destination in staged):
        baseline = generations / f"baseline-{os.getpid()}"
        baseline.mkdir()
        for destination, candidate in staged.items():
            prior = baseline / candidate.name
            if destination.exists():
                prior.write_bytes(destination.read_bytes())
            else:
                prior.write_bytes(b"")
        selector = control / f".current.{os.getpid()}"
        selector.symlink_to(os.path.relpath(baseline, control))
        os.replace(selector, current)
        previous = os.readlink(current)
    else:
        previous = None

    for index, (destination, candidate) in enumerate(staged.items(), start=1):
        expected = control / "current" / candidate.name
        if destination.is_symlink() and destination.resolve(strict=False) == expected.resolve(strict=False):
            continue
        if (
            previous is not None
            and destination.exists()
            and destination.read_bytes() != (control / previous / candidate.name).read_bytes()
        ):
            raise ValueError(f"control path is outside the active generation: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _relative_symlink(destination, expected)
        _publication_checkpoint(f"public-link-{index}")
    return previous


def _generation_for_path(path: Path, generations: Path) -> Path | None:
    try:
        relative = path.resolve(strict=False).relative_to(generations.resolve())
    except ValueError:
        return None
    return generations / relative.parts[0] if relative.parts else None


def _prune_generations(control: Path, schedule: ScheduleState | None) -> None:
    generations = control / "generations"
    protected: set[Path] = set()
    current = control / "current"
    if current.is_symlink():
        selected = _generation_for_path(current, generations)
        if selected is not None:
            protected.add(selected)
    if schedule is not None and schedule.live:
        if schedule.source is None:
            return
        source_generation = _generation_for_path(schedule.source, generations)
        if source_generation is not None:
            protected.add(source_generation)
        try:
            with schedule.source.open("rb") as stream:
                arguments = plistlib.load(stream).get("ProgramArguments", [])
        except (OSError, plistlib.InvalidFileException, AttributeError):
            return
        for argument in arguments:
            if isinstance(argument, str):
                generation = _generation_for_path(Path(argument), generations)
                if generation is not None:
                    protected.add(generation)
    for generation in generations.iterdir():
        if generation not in protected:
            shutil.rmtree(generation)


def _publish_control_files(
    staged: dict[Path, Path], launch_agent: Path, enabled: bool, reconcile: bool,
    control: Path, generation_name: str | None = None,
) -> bool | None:
    control.mkdir(parents=True, exist_ok=True)
    with (control / ".publish.lock").open("a+b") as lock:
        _publication_checkpoint("lock-attempt")
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _publish_control_files_locked(
            staged, launch_agent, enabled, reconcile, control, generation_name
        )


def _publish_control_files_locked(
    staged: dict[Path, Path], launch_agent: Path, enabled: bool, reconcile: bool,
    control: Path, generation_name: str | None,
) -> bool | None:
    previous_schedule = _schedule_state() if reconcile else None
    previous_generation = _prepare_control_lineage(staged, control)
    generations = control / "generations"
    generation = generations / (
        generation_name or f"generation-{os.getpid()}-{os.urandom(8).hex()}"
    )
    generation.mkdir()
    for candidate in staged.values():
        os.replace(candidate, generation / candidate.name)
    selector = control / f".current.{os.getpid()}"
    selector.symlink_to(os.path.relpath(generation, control))
    try:
        os.replace(selector, control / "current")
        _publication_checkpoint("generation-selected")
        live = _launchctl(launch_agent, enabled) if reconcile else None
        schedule = _schedule_state() if reconcile else None
        _prune_generations(control, schedule)
        return live
    except (OSError, ValueError) as primary:
        failures: list[str] = []
        try:
            rollback = control / f".current.{os.getpid()}.rollback"
            if previous_generation is None:
                (control / "current").unlink(missing_ok=True)
            else:
                rollback.symlink_to(previous_generation)
                os.replace(rollback, control / "current")
        except OSError as exc:
            failures.append(f"control rollback failed: {exc}")
        if previous_schedule is not None:
            try:
                restore_source = previous_schedule.source or launch_agent
                _launchctl(restore_source, previous_schedule.live)
            except (OSError, ValueError) as exc:
                failures.append(f"schedule restoration failed: {exc}")
        try:
            schedule = _schedule_state() if reconcile else None
            _prune_generations(control, schedule)
        except (OSError, ValueError):
            pass
        if failures:
            raise ValueError(f"publication failed: {primary}; {'; '.join(failures)}") from primary
        raise


def generate(
    repository: Path, home: Path, machine_root: Path | None = None
) -> tuple[Path, Path, bool | None, int]:
    repository = repository.resolve()
    home = home.resolve()
    machine_root = (machine_root or home / ".fkst" / "machine").expanduser().resolve()
    live_machine_root = (home / ".fkst" / "machine").resolve()
    declarations, declaration_set = _load_declarations(repository)
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

    profile = machine_root / "profile.toml"
    manifest = machine_root / "declarations.json"
    launch_agent = machine_root / "LaunchAgents" / "com.fkst.cadence.plist"
    profile.parent.mkdir(parents=True, exist_ok=True)
    (machine_root / "watch").mkdir(parents=True, exist_ok=True)
    launch_agent.parent.mkdir(parents=True, exist_ok=True)
    lock = _canonical_repository_file(repository, "fkst.lock", "lock")
    profile_text = _profile_text(declarations, machine_root)
    manifest_text = json.dumps({
        "schema": "fkst.ops.declaration-set.v2",
        "repository": str(repository),
        "input_set": {"path": DECLARATION_SET_NAME, "sha256": _sha256(declaration_set)},
        "lock": {"path": "fkst.lock", "sha256": _sha256(lock)},
        "declarations": [
            {"path": str(path.relative_to(repository)), "sha256": _sha256(path)}
            for path, _ in declarations
        ],
    }, sort_keys=True, separators=(",", ":")) + "\n"

    _hydrate(declarations, lock, machine_root)
    staging = Path(tempfile.mkdtemp(prefix=".control-", dir=machine_root))
    try:
        generation_name = f"generation-{os.getpid()}-{os.urandom(8).hex()}"
        generation_root = machine_root / "control" / "generations" / generation_name
        staged_profile = staging / "profile.toml"
        staged_manifest = staging / "declarations.json"
        staged_launch_agent = staging / "com.fkst.cadence.plist"
        staged_profile.write_text(profile_text, encoding="ascii")
        staged_manifest.write_text(manifest_text, encoding="ascii")
        for declaration_path, _ in declarations:
            load_and_resolve(declaration_path, staged_profile, lock)
        staged_launch_agent.write_text(
            _plist_text(
                repository, generation_root / "profile.toml",
                generation_root / "declarations.json", machine_root, interval,
            ),
            encoding="utf-8",
        )
        live = _publish_control_files(
            {
                profile: staged_profile,
                manifest: staged_manifest,
                launch_agent: staged_launch_agent,
            },
            launch_agent,
            enabled,
            machine_root == live_machine_root,
            machine_root / "control",
            generation_name,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return profile, launch_agent, live, interval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_repository", type=Path)
    parser.add_argument("--machine-state-root", type=Path)
    args = parser.parse_args(argv)
    try:
        profile, launch_agent, live, interval = generate(
            args.deployment_repository, Path.home(), args.machine_state_root
        )
    except (OSError, ValueError, ValidationError) as exc:
        print(f"artifact generation failed: {exc}", file=sys.stderr)
        return 2
    print(profile)
    print(launch_agent)
    schedule = "not-reconciled" if live is None else ("enabled" if live else "disabled")
    live_text = "not-queried" if live is None else ("yes" if live else "no")
    print(f"cadence_schedule={schedule} live={live_text} interval_seconds={interval}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
