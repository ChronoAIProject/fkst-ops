"""Hydrate branch-operated sources and packages-derived engine builds."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
from typing import Any
from urllib.parse import urlsplit

from ops.revision_derivation import (
    build_is_current,
    engine_product,
    resolve_pair,
    write_build_receipt,
)
from schema.validator import resolve_machine_default


@dataclass(frozen=True)
class BranchCheckout:
    url: str
    branch: str


@dataclass(frozen=True)
class EngineCheckout:
    url: str
    revision: str


@dataclass(frozen=True)
class EngineBuild:
    checkout: str
    command: tuple[str, ...]
    revision: str


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    ).stdout.strip()


def _clean(root: Path) -> bool:
    try:
        return not _git(root, "status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return False


def _safe_source_url(url: str, destination: Path) -> None:
    parsed = urlsplit(url)
    if parsed.scheme in {"http", "https"} and (
        parsed.username is not None or parsed.password is not None
    ):
        raise ValueError(f"checkout {destination.name} has a credential-bearing source URL")


def _require_source_identity(destination: Path, declared_url: str) -> None:
    try:
        observed_url = _git(destination, "remote", "get-url", "origin")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f"CHECKOUT_SOURCE_UNREADABLE: checkout {destination.name} has no readable origin"
        ) from exc
    if observed_url != declared_url:
        raise ValueError(
            f"CHECKOUT_SOURCE_MISMATCH: checkout {destination.name} origin does not match "
            "its declared source"
        )


def _branch_checkout_valid(root: Path, branch: str) -> bool:
    try:
        remote = f"refs/remotes/origin/{branch}"
        return (
            _git(root, "branch", "--show-current") == branch
            and bool(_git(root, "rev-parse", "--verify", remote))
            and subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", remote, "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            ).returncode
            == 0
            and _clean(root)
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def _engine_checkout_valid(root: Path, revision: str) -> bool:
    try:
        return (
            _git(root, "rev-parse", "--verify", "HEAD^{commit}") == revision
            and _git(root, "branch", "--show-current") == ""
            and _clean(root)
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def _materialise_branch_checkout(destination: Path, spec: BranchCheckout) -> None:
    _safe_source_url(spec.url, destination)
    if destination.exists() or destination.is_symlink():
        if not destination.is_dir() or not _clean(destination):
            raise ValueError(
                f"checkout {destination} does not match branch-operated state {spec.branch}; "
                "refusing to replace existing work"
            )
        _require_source_identity(destination, spec.url)
        try:
            if _git(destination, "branch", "--show-current") != spec.branch:
                raise ValueError(
                    f"checkout {destination} does not match branch-operated state {spec.branch}; "
                    "refusing to replace existing work"
                )
            remote = f"refs/remotes/origin/{spec.branch}"
            _git(
                destination,
                "fetch",
                "--no-tags",
                "origin",
                f"+refs/heads/{spec.branch}:{remote}",
            )
            if _branch_checkout_valid(destination, spec.branch):
                return
            if subprocess.run(
                ["git", "-C", str(destination), "merge-base", "--is-ancestor", "HEAD", remote],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            ).returncode == 0:
                _git(destination, "merge", "--ff-only", remote)
            if _branch_checkout_valid(destination, spec.branch):
                return
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError(
                f"checkout {destination} cannot refresh branch {spec.branch}: {exc}"
            ) from exc
        raise ValueError(
            f"checkout {destination} diverges from branch-operated state {spec.branch}; "
            "refusing to replace existing work"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", spec.url, str(temporary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        remote = f"refs/remotes/origin/{spec.branch}"
        if subprocess.run(
            ["git", "-C", str(temporary), "rev-parse", "--verify", remote],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        ).returncode != 0:
            raise ValueError(
                f"checkout {destination.name} remote integration branch {spec.branch} is missing"
            )
        _git(temporary, "checkout", "--quiet", "-B", spec.branch, remote)
        _require_source_identity(temporary, spec.url)
        if not _branch_checkout_valid(temporary, spec.branch):
            raise ValueError(
                f"checkout {destination.name} could not materialise branch {spec.branch}"
            )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _materialise_engine_checkout(destination: Path, spec: EngineCheckout) -> None:
    _safe_source_url(spec.url, destination)
    if destination.exists() or destination.is_symlink():
        if not destination.is_dir() or not _clean(destination):
            raise ValueError(
                f"engine checkout {destination} is not clean; refusing exact-revision update"
            )
        _require_source_identity(destination, spec.url)
        if _engine_checkout_valid(destination, spec.revision):
            return
        try:
            branch = _git(destination, "branch", "--show-current")
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError(f"cannot inspect engine checkout {destination}: {exc}") from exc
        if branch:
            raise ValueError(
                f"engine checkout {destination} is attached to branch {branch}; refusing exact-revision update"
            )
        try:
            _git(destination, "fetch", "--no-tags", "origin", spec.revision)
            _git(destination, "checkout", "--quiet", "--detach", spec.revision)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError(
                f"engine checkout {destination} cannot fetch exact revision {spec.revision}: {exc}"
            ) from exc
        if not _engine_checkout_valid(destination, spec.revision):
            raise ValueError(
                f"engine checkout {destination} does not match exact revision {spec.revision}"
            )
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", spec.url, str(temporary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        _git(temporary, "fetch", "--no-tags", "origin", spec.revision)
        _git(temporary, "checkout", "--quiet", "--detach", spec.revision)
        _require_source_identity(temporary, spec.url)
        if not _engine_checkout_valid(temporary, spec.revision):
            raise ValueError(
                f"engine checkout {destination.name} does not match exact revision {spec.revision}"
            )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _materialise_engine_build(checkout: Path, binary: Path, spec: EngineBuild) -> None:
    command = list(spec.command)
    if build_is_current(binary, checkout, spec.revision, command):
        return
    result = subprocess.run(command, cwd=checkout, check=False)
    if result.returncode != 0:
        raise ValueError(f"engine build failed with exit code {result.returncode}: {command[0]}")
    if not _engine_checkout_valid(checkout, spec.revision):
        raise ValueError(
            f"engine build changed source revision; expected detached {spec.revision}"
        )
    product = engine_product(checkout, command)
    if not product.is_file() or not os.access(product, os.X_OK):
        raise ValueError(f"engine build did not produce executable: {product}")
    binary.parent.mkdir(parents=True, exist_ok=True)
    pointer = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
    pointer.unlink(missing_ok=True)
    pointer.symlink_to(product)
    os.replace(pointer, binary)
    write_build_receipt(binary, spec.revision, command)


def hydrate(
    declarations: list[tuple[Path, dict[str, Any]]],
    lock_path: Path,
    machine_root: Path,
    tools: dict[str, str],
    machine_defaults: dict[str, str],
) -> None:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    sources = {entry["id"]: entry for entry in lock.get("external_source", [])}
    branch_specs: dict[str, BranchCheckout] = {}
    preserved_roots: set[str] = set()
    for declaration_path, declaration in declarations:
        for index, deployment in enumerate(declaration["deployment"]):
            machine = deployment["machine"]
            branch = resolve_machine_default(
                deployment["integration"]["integration_branch"],
                machine_defaults,
                f"declaration.deployment[{index}].integration.integration_branch",
            )
            for role in ("target", "platform"):
                logical = machine[f"{role}_checkout"]
                source_id = deployment["sources"][role]["lock_ref"]
                try:
                    entry = sources[source_id]
                    spec = BranchCheckout(entry["git"], branch)
                except (KeyError, TypeError) as exc:
                    raise ValueError(
                        f"{declaration_path} deployment[{index}] source {source_id} is incomplete"
                    ) from exc
                if logical in branch_specs and branch_specs[logical] != spec:
                    raise ValueError(
                        f"checkout root {logical} is assigned conflicting sources or branches"
                    )
                branch_specs[logical] = spec
            for field in ("durable", "runtime", "logs", "rate_pool"):
                preserved_roots.add(machine[field])
    for logical, spec in branch_specs.items():
        _materialise_branch_checkout(machine_root / "roots" / logical, spec)

    engine_checkouts: dict[str, EngineCheckout] = {}
    engine_builds: dict[str, EngineBuild] = {}
    for declaration_path, declaration in declarations:
        providers = {provider["id"]: provider for provider in declaration["provider"]}
        for index, deployment in enumerate(declaration["deployment"]):
            machine = deployment["machine"]
            derivation = deployment.get("engine_revision")
            if not isinstance(derivation, dict):
                raise ValueError(
                    f"{declaration_path} deployment[{index}].engine_revision "
                    "is required"
                )
            if not isinstance(derivation.get("path"), str):
                raise ValueError(
                    f"{declaration_path} deployment[{index}].engine_revision.path is required"
                )
            derivation_root = machine_root / "roots" / machine["platform_checkout"]
            pair = resolve_pair(derivation_root, derivation["path"])
            source_id = deployment["sources"]["engine"]["lock_ref"]
            try:
                engine_source = sources[source_id]["git"]
            except (KeyError, TypeError) as exc:
                raise ValueError(
                    f"{declaration_path} deployment[{index}] engine source {source_id} is incomplete"
                ) from exc
            checkout_name = machine["engine_checkout"]
            checkout_spec = EngineCheckout(engine_source, pair.engine_revision)
            if checkout_name in engine_checkouts and engine_checkouts[checkout_name] != checkout_spec:
                raise ValueError(
                    f"engine checkout {checkout_name} is assigned conflicting exact revisions"
                )
            engine_checkouts[checkout_name] = checkout_spec
            provider = providers[deployment["providers"]["engine"]]
            command = list(provider["configuration"]["build_command"])
            if Path(command[0]).name == command[0]:
                command[0] = tools[command[0]]
            build_spec = EngineBuild(checkout_name, tuple(command), pair.engine_revision)
            binary_name = machine["engine_binary"]
            if binary_name in engine_builds and engine_builds[binary_name] != build_spec:
                raise ValueError(
                    f"engine binary {binary_name} has conflicting declared revisions or build inputs"
                )
            engine_builds[binary_name] = build_spec

    for logical, spec in engine_checkouts.items():
        _materialise_engine_checkout(machine_root / "roots" / logical, spec)
    for logical in preserved_roots:
        (machine_root / "roots" / logical).mkdir(parents=True, exist_ok=True)
    for binary_name, spec in engine_builds.items():
        _materialise_engine_build(
            machine_root / "roots" / spec.checkout,
            machine_root / "bin" / binary_name,
            spec,
        )
