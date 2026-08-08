#!/usr/bin/env python3
"""Maintain deployment host fkst.workspace.toml platform package entries."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


def fail(message: str) -> None:
    print(f"error: {message}")
    raise SystemExit(1)


def table_array(data: dict[str, object], key: str) -> list[dict[str, object]]:
    value = data.get(key, [])
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        fail(f"fkst.workspace.toml {key} must be a table array")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            fail(f"fkst.workspace.toml {key} entries must be tables")
        rows.append(item)
    return rows


def package_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        fail(f"fkst.workspace.toml {field} must be a string array")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            fail(f"fkst.workspace.toml {field} must contain only non-empty strings")
        out.append(item)
    return out


def reject_duplicates(values: list[str], field: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        fail(f"{field} contains duplicate package names: {' '.join(duplicates)}")


def external_source_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        if line.strip() != "[[external_sources]]":
            continue
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            stripped = lines[cursor].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end = cursor
                break
        blocks.append((index, end))
    return blocks


def load_block(lines: list[str], start: int, end: int) -> dict[str, object] | None:
    try:
        data = tomllib.loads("".join(lines[start:end]))
    except tomllib.TOMLDecodeError:
        return None
    sources = table_array(data, "external_sources")
    if len(sources) != 1:
        fail("external_sources block parser expected exactly one source")
    return sources[0]


def package_assignment_range(lines: list[str], start: int, end: int) -> tuple[int, int, str] | None:
    pattern = re.compile(r"^(\s*)packages\s*=")
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if not match:
            continue
        cursor = index + 1
        rhs = lines[index].split("=", 1)[1]
        depth = rhs.count("[") - rhs.count("]")
        while depth > 0 and cursor < end:
            depth += lines[cursor].count("[") - lines[cursor].count("]")
            cursor += 1
        return index, cursor, match.group(1)
    return None


def insertion_point(lines: list[str], start: int, end: int) -> tuple[int, str]:
    pattern = re.compile(r"^(\s*)git\s*=")
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match:
            return index + 1, match.group(1)
    return end, ""


def normalized_git_url(value: str) -> tuple[str, str, str, str]:
    # Repository identity ignores transport scheme and user info, lowercases the host,
    # and removes trailing slashes and one trailing `.git`. It deliberately does not
    # equate host aliases, ports, or path case, nor discard query strings or fragments.
    parsed = urlparse(value)
    if not parsed.scheme and ":" in value and not value.startswith(("/", "./", "../")):
        scp_host, scp_path = value.split(":", 1)
        parsed = urlparse(f"ssh://{scp_host}/{scp_path}")
    if parsed.scheme and parsed.scheme != "file":
        host = (parsed.hostname or "").lower()
        if parsed.port is not None:
            host += f":{parsed.port}"
        path = unquote(parsed.path).rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return host, path.lstrip("/"), parsed.query, parsed.fragment
    path_value = unquote(parsed.path) if parsed.scheme == "file" else value
    path = str(Path(path_value).resolve()).rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return "", path, "", ""


def source_id(source: dict[str, object]) -> str:
    value = source.get("id")
    return value if isinstance(value, str) else repr(value)


def platform_source(data: dict[str, object], git_url: str, name: str) -> tuple[str, dict[str, object]]:
    sources = table_array(data, "external_sources")
    wanted = normalized_git_url(git_url)
    matches = [
        source for source in sources
        if isinstance(source.get("git"), str) and normalized_git_url(source["git"]) == wanted
    ]
    observed = [source_id(source) for source in sources]
    if len(matches) != 1:
        found = "no matches" if not matches else f"{len(matches)} matches with ids {[source_id(source) for source in matches]}"
        fail(f"{name}: target fkst.workspace.toml platform source lookup for git URL {git_url!r} found {found}; observed external source ids: {observed}")
    matched_id = matches[0].get("id")
    if not isinstance(matched_id, str) or not matched_id:
        fail(f"{name}: external source matching git URL {git_url!r} must declare a non-empty id; observed external source ids: {observed}")
    return matched_id, matches[0]


def platform_block(lines: list[str], source_id: str) -> tuple[int, int] | None:
    for start, end in external_source_blocks(lines):
        source = load_block(lines, start, end)
        if source is not None and source.get("id") == source_id:
            return start, end
    return None


def render_with_packages(text: str, requested: list[str], source_id: str) -> str:
    lines = text.splitlines(keepends=True)
    block = platform_block(lines, source_id)
    if block is None:
        fail(f"could not locate external_sources(id={source_id}) in fkst.workspace.toml")
    start, end = block
    new_line = f"{json.dumps(requested)}\n"
    assignment = package_assignment_range(lines, start, end)
    if assignment is None:
        insert_at, indent = insertion_point(lines, start, end)
        return "".join(lines[:insert_at] + [f"{indent}packages = {new_line}"] + lines[insert_at:])
    package_start, package_end, indent = assignment
    return "".join(lines[:package_start] + [f"{indent}packages = {new_line}"] + lines[package_end:])


def parse_workspace(text: str, workspace_path: Path, name: str) -> dict[str, Any]:
    try:
        workspace = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        fail(f"{name}: invalid target fkst.workspace.toml: {workspace_path}: {exc}")
    if not isinstance(workspace, dict):
        fail(f"{name}: target fkst.workspace.toml must be a TOML table")
    return workspace


def sync(name: str, host: Path, requested: list[str], git_url: str) -> None:
    workspace_path = host / "fkst.workspace.toml"
    if not workspace_path.is_file():
        fail(f"{name}: target fkst.workspace.toml is required for deployment platform sync: {workspace_path}")
    reject_duplicates(requested, "DEVLOOP_PKGS")
    text = workspace_path.read_text(encoding="utf-8")
    workspace = parse_workspace(text, workspace_path, name)
    source_id, source = platform_source(workspace, git_url, name)
    field = f"external_sources(id={source_id}).packages"
    declared_before = package_list(source.get("packages", []), field)
    reject_duplicates(declared_before, field)
    if declared_before == requested:
        return

    new_text = render_with_packages(text, requested, source_id)
    if new_text != text:
        tmp_path = workspace_path.with_name(workspace_path.name + ".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(workspace_path)

    synced = parse_workspace(new_text, workspace_path, name)
    synced_source_id, source = platform_source(synced, git_url, name)
    if synced_source_id != source_id:
        fail(f"{name}: synced fkst.workspace.toml changed the platform external source id")
    declared = package_list(source.get("packages", []), field)
    reject_duplicates(declared, field)
    if declared != requested:
        fail(f"{name}: synced platform package list does not match DEVLOOP_PKGS")


def platform_packages(name: str, host: Path, pkgsrc: Path, git_url: str) -> list[str]:
    workspace_path = host / "fkst.workspace.toml"
    if not workspace_path.is_file():
        fail(f"{name}: target fkst.workspace.toml is required for deployment platform package selection: {workspace_path}")
    workspace = parse_workspace(workspace_path.read_text(encoding="utf-8"), workspace_path, name)
    if host.resolve() == pkgsrc.resolve():
        packages: list[str] = []
        for package in table_array(workspace, "package"):
            name_value = package.get("name")
            source = package.get("source", "workspace")
            if isinstance(name_value, str) and source == "workspace":
                packages.append(name_value)
        reject_duplicates(packages, "package.name")
        if not packages:
            fail(f"{name}: self-host fkst.workspace.toml must declare deployment platform packages as [[package]] entries")
        return packages

    source_id, source = platform_source(workspace, git_url, name)
    field = f"external_sources(id={source_id}).packages"
    declared = package_list(source.get("packages", []), field)
    reject_duplicates(declared, field)
    if not declared:
        fail(f"{name}: external_sources(id={source_id}).packages must not be empty")
    return declared


def is_generated_scratch(worktree: Path, requested: list[str], git_url: str) -> bool:
    current_path = worktree / "fkst.workspace.toml"
    if not current_path.is_file():
        return False
    head = subprocess.run(
        ["git", "-C", str(worktree), "show", "HEAD:fkst.workspace.toml"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if head.returncode != 0:
        return False
    head_workspace = parse_workspace(head.stdout, current_path, str(worktree))
    source_id, _source = platform_source(head_workspace, git_url, str(worktree))
    return current_path.read_text(encoding="utf-8") == render_with_packages(head.stdout, requested, source_id)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        fail("usage: workspace_manifest.py {sync|platform-packages|is-generated-scratch} ...")
    cmd = argv[1]
    if cmd == "sync":
        if len(argv) != 6:
            fail("usage: workspace_manifest.py sync <name> <host> <packages> <platform-git-url>")
        sync(argv[2], Path(argv[3]), [item for item in argv[4].split() if item], argv[5])
        return 0
    if cmd == "platform-packages":
        if len(argv) != 6:
            fail("usage: workspace_manifest.py platform-packages <name> <host> <pkgsrc> <platform-git-url>")
        print(" ".join(platform_packages(argv[2], Path(argv[3]), Path(argv[4]), argv[5])))
        return 0
    if cmd == "is-generated-scratch":
        if len(argv) != 5:
            fail("usage: workspace_manifest.py is-generated-scratch <worktree> <packages> <platform-git-url>")
        return 0 if is_generated_scratch(Path(argv[2]), [item for item in argv[3].split() if item], argv[4]) else 1
    fail(f"unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
