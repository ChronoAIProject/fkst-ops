#!/usr/bin/env python3
"""Generate all machine-local artifacts for one deployment repository."""

from __future__ import annotations

import argparse
from html import escape
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tomllib
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema.validator import SCHEMA_ID, ValidationError, load_and_resolve


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "watch" / "com.fkst.cadence.plist.template"


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


def _github_login() -> str:
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        text=True,
        capture_output=True,
        check=False,
    )
    login = result.stdout.strip()
    if result.returncode or not login or "\n" in login:
        detail = result.stderr.strip() or "authenticated session returned no login"
        raise ValueError(f"cannot discover GitHub CLI identity: {detail}")
    return login


def _quoted(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=True)


def _profile_text(
    declarations: list[tuple[Path, dict[str, Any]]], home: Path, login: str
) -> str:
    base = home / ".fkst" / "machine"
    roots: dict[str, str] = {}
    binaries: dict[str, str] = {}
    credentials: dict[str, str] = {}
    sets: dict[str, list[str]] = {}

    for _, declaration in declarations:
        for deployment in declaration["deployment"]:
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
            credentials.setdefault(machine["bot_login"], login)
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


def generate(repository: Path, home: Path) -> tuple[Path, Path]:
    repository = repository.resolve()
    home = home.resolve()
    declarations = _load_declarations(repository)
    intervals = {document.get("cadence_interval_seconds") for _, document in declarations}
    if len(intervals) != 1:
        raise ValueError("all deployment declarations must use one cadence_interval_seconds")
    interval = intervals.pop()
    if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
        raise ValueError("cadence_interval_seconds must be a positive integer")

    profile = repository / ".fkst" / "machine-profile.toml"
    launch_agent = home / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    profile.parent.mkdir(parents=True, exist_ok=True)
    (home / ".fkst" / "watch").mkdir(parents=True, exist_ok=True)
    launch_agent.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(_profile_text(declarations, home, _github_login()), encoding="ascii")

    lock = repository / "fkst.lock"
    for declaration_path, _ in declarations:
        load_and_resolve(declaration_path, profile, lock)

    launch_agent.write_text(
        _plist_text(repository, profile, home, interval), encoding="utf-8"
    )
    return profile, launch_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deployment_repository", type=Path)
    args = parser.parse_args(argv)
    try:
        profile, launch_agent = generate(args.deployment_repository, Path.home())
    except (OSError, ValueError, ValidationError) as exc:
        print(f"artifact generation failed: {exc}", file=sys.stderr)
        return 2
    print(profile)
    print(launch_agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
