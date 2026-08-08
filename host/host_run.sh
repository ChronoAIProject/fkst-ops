#!/usr/bin/env bash
# Host-run contract helpers for scripts/run.sh supervise.

HOST_RUN_PROJECT_ROOT=""
HOST_RUN_PLATFORM_ROOT=""
HOST_RUN_LOCAL_PACKAGES_ROOT=""
HOST_RUN_PLATFORM_PACKAGES=""
HOST_RUN_HOST_PACKAGES=""
HOST_RUN_DURABLE_ROOT=""
HOST_RUN_RUNTIME_ROOT=""
HOST_RUN_RUNTIME_BASE=""
HOST_RUN_RUNTIME_LABEL=""
HOST_RUN_RUNTIME_IS_EXPLICIT=0
HOST_RUN_RESTART=0
HOST_RUN_PACKAGE_ROOTS=()

host_run_usage() {
  cat >&2 <<'EOF'
usage: scripts/run.sh supervise --project-root <HOST> --platform-root <PKGSRC> --platform-packages "<names>" [--host-packages "<names>"] --durable-root <path> [--runtime-root <fresh-scratch-root>] [--restart]
   or: scripts/run.sh supervise <package>
EOF
}

host_run_abs_path() {
  local path="$1"
  case "$path" in
    /*) printf '%s\n' "$path" ;;
    *) printf '%s/%s\n' "$(pwd -P)" "$path" ;;
  esac
}

host_run_same_path() {
  local left="$1" right="$2" left_phys right_phys
  left_phys="$(cd "$left" 2>/dev/null && pwd -P)" || return 1
  right_phys="$(cd "$right" 2>/dev/null && pwd -P)" || return 1
  [ "$left_phys" = "$right_phys" ]
}

host_run_export_codex_repository_roots() {
  local root physical existing duplicate
  local roots=()
  for root in "$@"; do
    case "$root" in
      ""|*$'\n'*|*$'\r'*)
        echo "error: codex repository roots must be non-empty single-line paths" >&2
        return 1
        ;;
    esac
    physical="$(cd "$root" 2>/dev/null && pwd -P)" || {
      echo "error: codex repository root does not exist: $root" >&2
      return 1
    }
    duplicate=0
    for existing in ${roots[@]+"${roots[@]}"}; do
      if [ "$existing" = "$physical" ]; then
        duplicate=1
        break
      fi
    done
    if [ "$duplicate" -eq 0 ]; then
      roots+=("$physical")
    fi
  done
  if [ "${#roots[@]}" -eq 0 ]; then
    echo "error: at least one codex repository root is required" >&2
    return 1
  fi
  printf -v FKST_CODEX_REPOSITORY_ROOTS '%s\n' "${roots[@]}"
  export FKST_CODEX_REPOSITORY_ROOTS
}

host_run_resolve_target_platform_roots() {
  local output line
  output="$(python3 - "$HOST_RUN_PROJECT_ROOT" "$HOST_RUN_PLATFORM_PACKAGES" "$HOST_RUN_PLATFORM_ROOT" <<'PY'
import re
import shlex
import subprocess
import sys
import tomllib
from glob import glob
from pathlib import Path
from urllib.parse import unquote, urlparse

ID_RE = re.compile(r"[A-Za-z0-9._-]+")
REV_RE = re.compile(r"[0-9a-fA-F]{40}")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def git_output(args: list[str], *, cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except FileNotFoundError:
        fail(f"{shlex.join(['git', *args])} could not start (cwd={cwd}): git executable not found")
    except subprocess.CalledProcessError as exc:
        message = f"{shlex.join(['git', *args])} failed with exit {exc.returncode} (cwd={cwd})"
        stderr = exc.stderr.strip()
        if stderr:
            message = f"{message}: {stderr}"
        fail(message)
    return result.stdout.strip()


def git_output_optional(args: list[str], *, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        fail("git is required to resolve host external sources")
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def list_of_tables(data: dict[str, object], key: str) -> list[dict[str, object]]:
    entries = data.get(key, [])
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        fail(f"fkst.workspace.toml {key} must be a table array")
    out: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail(f"fkst.workspace.toml {key} entries must be tables")
        out.append(entry)
    return out


def string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        fail(f"fkst.workspace.toml {field} must be a string array")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            fail(f"fkst.workspace.toml {field} must contain only non-empty strings")
        out.append(item)
    return out


def lock_sources(lock_path: Path) -> list[dict[str, object]]:
    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        fail(f"invalid fkst lockfile: {lock_path}: {exc}")
    sources = data.get("external_source", [])
    if isinstance(sources, dict):
        sources = [sources]
    if not isinstance(sources, list):
        fail("fkst.lock external_source must be a table array")
    for source in sources:
        if not isinstance(source, dict):
            fail("fkst.lock external_source entries must be tables")
    return sources


def validate_source_id(source: dict[str, object], context: str) -> str:
    source_id = source.get("id")
    if not isinstance(source_id, str) or not ID_RE.fullmatch(source_id) or source_id in {".", ".."}:
        fail(f"{context} has invalid id")
    return source_id


def record_unique_source_id(source_id: str, locations: dict[str, str], location: str) -> None:
    first_location = locations.get(source_id)
    if first_location is not None:
        fail(f"duplicate source id '{source_id}' at {first_location} and {location}")
    locations[source_id] = location


def validate_lock_source(source: dict[str, object], source_id: str) -> tuple[str, str]:
    git_url = source.get("git")
    resolved = source.get("resolved")
    rev = resolved.get("rev") if isinstance(resolved, dict) else None
    if not isinstance(git_url, str) or not git_url:
        fail(f"fkst.lock external_source(id={source_id}) is missing git")
    if not isinstance(rev, str) or not REV_RE.fullmatch(rev):
        fail(f"fkst.lock external_source(id={source_id}) is missing resolved.rev as a full git SHA")
    return git_url, rev.lower()


def is_scp_like_url(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.-]+@[^:]+:", value))


def git_ref_names(value: str, *, base: Path) -> set[str]:
    text = value.rstrip("/")
    refs = {text}
    parsed = urlparse(text)
    if parsed.scheme == "file":
        refs.add(str(Path(unquote(parsed.path)).resolve()))
    elif "://" not in text and not is_scp_like_url(text):
        path = Path(text)
        if not path.is_absolute():
            path = base / path
        refs.add(str(path.resolve()))
    return refs


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def trusted_platform_identity(platform_root: Path) -> tuple[str, set[str]]:
    if not platform_root.is_dir():
        fail(f"trusted --platform-root does not exist: {platform_root}")
    head = git_output(["rev-parse", "HEAD"], cwd=platform_root).lower()
    if not REV_RE.fullmatch(head):
        fail(f"trusted --platform-root HEAD is not a full git SHA: {platform_root}")
    top = Path(git_output(["rev-parse", "--show-toplevel"], cwd=platform_root)).resolve()
    refs = git_ref_names(str(top), base=top)
    refs.update(git_ref_names(str(platform_root), base=top))
    origin = git_output_optional(["config", "--get", "remote.origin.url"], cwd=platform_root)
    if origin:
        refs.update(git_ref_names(origin, base=top))
    return head, refs


def read_workspace(workspace_path: Path) -> dict[str, object]:
    if not workspace_path.is_file():
        fail(f"target fkst.workspace.toml is required for host supervise: {workspace_path}")
    try:
        data = tomllib.loads(workspace_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        fail(f"invalid target fkst.workspace.toml: {workspace_path}: {exc}")
    if not isinstance(data, dict):
        fail("target fkst.workspace.toml must be a TOML table")
    return data


project_root = Path(sys.argv[1]).resolve()
requested_packages = [item for item in sys.argv[2].split() if item]
trusted_platform_root = Path(sys.argv[3]).resolve()
workspace_path = project_root / "fkst.workspace.toml"
workspace = read_workspace(workspace_path)
workspace_packages: dict[str, str] = {}
workspace_table = workspace.get("workspace", {})
if isinstance(workspace_table, dict):
    unit_globs = workspace_table.get("units", [])
    if unit_globs is None:
        unit_globs = []
    for pattern in string_list(unit_globs, "workspace.units"):
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            fail("fkst.workspace.toml workspace.units entries must be safe relative globs")
        for match in glob(str(project_root / pattern)):
            package_root = Path(match)
            if package_root.is_dir() and (package_root / "fkst.toml").is_file():
                workspace_packages[package_root.name] = "workspace"
for package in list_of_tables(workspace, "package"):
    name = package.get("name")
    source = package.get("source", "workspace")
    if isinstance(name, str) and source == "workspace":
        workspace_packages[name] = "workspace"

external_sources: dict[str, dict[str, object]] = {}
workspace_source_locations: dict[str, str] = {}
for entry_number, source in enumerate(list_of_tables(workspace, "external_sources"), start=1):
    source_id = validate_source_id(source, "fkst.workspace.toml external_sources")
    record_unique_source_id(
        source_id,
        workspace_source_locations,
        f"fkst.workspace.toml external_sources[{entry_number}]",
    )
    git_url = source.get("git")
    if not isinstance(git_url, str) or not git_url:
        fail(f"fkst.workspace.toml external_sources(id={source_id}) is missing git")
    packages = string_list(source.get("packages", []), f"external_sources(id={source_id}).packages")
    external_sources[source_id] = {"git": git_url, "packages": packages}

lock_by_id: dict[str, tuple[str, str]] = {}
selected: list[tuple[str, str, str | None]] = []
needed_external_source_ids: set[str] = set()

for package in requested_packages:
    matches: list[tuple[str, str | None]] = []
    if package in workspace_packages:
        matches.append(("workspace", None))
    for source_id, source in external_sources.items():
        if package in source["packages"]:
            matches.append(("external", source_id))
    if not matches:
        fail(f"target fkst.workspace.toml does not declare platform package '{package}'")
    if len(matches) > 1:
        fail(f"ambiguous target fkst.workspace.toml platform package '{package}'")
    kind, source_id = matches[0]
    selected.append((package, kind, source_id))
    if source_id is not None:
        needed_external_source_ids.add(source_id)

if needed_external_source_ids:
    lock_path = project_root / "fkst.lock"
    if not lock_path.is_file():
        fail(f"target fkst.lock is required for external platform packages: {lock_path}")
    lock_source_locations: dict[str, str] = {}
    for entry_number, source in enumerate(lock_sources(lock_path), start=1):
        source_id = validate_source_id(source, "fkst.lock external_source")
        record_unique_source_id(
            source_id,
            lock_source_locations,
            f"fkst.lock external_source[{entry_number}]",
        )
        git_url, rev = validate_lock_source(source, source_id)
        lock_by_id[source_id] = (git_url, rev)

source_roots: dict[str, Path] = {}
trusted_platform_refs: set[str] = set()
if needed_external_source_ids:
    _, trusted_platform_refs = trusted_platform_identity(trusted_platform_root)

for source_id in sorted(needed_external_source_ids):
    if source_id not in lock_by_id:
        fail(f"fkst.lock has no external_source(id={source_id}) for target platform packages")
    expected_git = external_sources[source_id]["git"]
    git_url, _rev = lock_by_id[source_id]
    if git_url != expected_git:
        fail(f"fkst.workspace.toml external_sources(id={source_id}) git does not match fkst.lock")
    if trusted_platform_refs.isdisjoint(git_ref_names(git_url, base=project_root)):
        fail(f"fkst.workspace.toml external_sources(id={source_id}) git does not match trusted --platform-root")
    source_roots[source_id] = trusted_platform_root

package_roots: list[Path] = []
platform_roots: set[Path] = set()
for package, kind, source_id in selected:
    if kind == "workspace":
        if not same_path(project_root, trusted_platform_root):
            fail(f"workspace platform package '{package}' requires trusted --platform-root")
        root = project_root / "packages" / package
        platform_roots.add(project_root)
    else:
        assert source_id is not None
        root = source_roots[source_id] / "packages" / package
        platform_roots.add(source_roots[source_id])
    if not root.is_dir():
        fail(f"missing target platform package '{package}' at {root}")
    package_roots.append(root)

if len(platform_roots) != 1:
    fail("target platform packages must resolve to exactly one workspace or external source")

print(f"PLATFORM_ROOT={next(iter(platform_roots))}")
for root in package_roots:
    print(f"PACKAGE_ROOT={root}")
PY
)" || return $?

  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      PLATFORM_ROOT=*) HOST_RUN_PLATFORM_ROOT="${line#PLATFORM_ROOT=}" ;;
      PACKAGE_ROOT=*) HOST_RUN_PACKAGE_ROOTS+=("${line#PACKAGE_ROOT=}") ;;
      *) echo "error: unexpected platform resolver output: $line" >&2; return 1 ;;
    esac
  done <<< "$output"
}

host_run_parse_supervise_args() {
  HOST_RUN_PROJECT_ROOT=""
  HOST_RUN_PLATFORM_ROOT=""
  HOST_RUN_LOCAL_PACKAGES_ROOT=""
  HOST_RUN_PLATFORM_PACKAGES=""
  HOST_RUN_HOST_PACKAGES=""
  HOST_RUN_DURABLE_ROOT=""
  HOST_RUN_RUNTIME_ROOT=""
  HOST_RUN_RUNTIME_BASE=""
  HOST_RUN_RUNTIME_LABEL=""
  HOST_RUN_RUNTIME_IS_EXPLICIT=0
  HOST_RUN_RESTART=0

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --project-root)
        [ "$#" -ge 2 ] || { echo "error: --project-root requires a path" >&2; return 2; }
        HOST_RUN_PROJECT_ROOT="$2"; shift 2 ;;
      --platform-root)
        [ "$#" -ge 2 ] || { echo "error: --platform-root requires a path" >&2; return 2; }
        HOST_RUN_PLATFORM_ROOT="$2"; shift 2 ;;
      --local-packages)
        [ "$#" -ge 2 ] || { echo "error: --local-packages requires a path" >&2; return 2; }
        HOST_RUN_LOCAL_PACKAGES_ROOT="$2"; shift 2 ;;
      --platform-packages)
        [ "$#" -ge 2 ] || { echo "error: --platform-packages requires a package list" >&2; return 2; }
        HOST_RUN_PLATFORM_PACKAGES="$2"; shift 2 ;;
      --host-packages)
        [ "$#" -ge 2 ] || { echo "error: --host-packages requires a package list" >&2; return 2; }
        HOST_RUN_HOST_PACKAGES="$2"; shift 2 ;;
      --durable-root)
        [ "$#" -ge 2 ] || { echo "error: --durable-root requires a path" >&2; return 2; }
        HOST_RUN_DURABLE_ROOT="$2"; shift 2 ;;
      --runtime-root)
        [ "$#" -ge 2 ] || { echo "error: --runtime-root requires a path" >&2; return 2; }
        HOST_RUN_RUNTIME_BASE="$2"; HOST_RUN_RUNTIME_IS_EXPLICIT=1; shift 2 ;;
      --restart)
        HOST_RUN_RESTART=1; shift ;;
      -h|--help)
        host_run_usage; return 2 ;;
      *)
        echo "error: unknown supervise option: $1" >&2
        host_run_usage
        return 2 ;;
    esac
  done

  [ -n "$HOST_RUN_PROJECT_ROOT" ] || { echo "error: --project-root is required" >&2; return 2; }
  [ -n "$HOST_RUN_PLATFORM_ROOT" ] || { echo "error: --platform-root is required" >&2; return 2; }
  [ -n "$HOST_RUN_PLATFORM_PACKAGES" ] || { echo "error: --platform-packages is required" >&2; return 2; }
  [ -n "$HOST_RUN_DURABLE_ROOT" ] || { echo "error: --durable-root is required for explicit supervise" >&2; return 2; }

  HOST_RUN_PROJECT_ROOT="$(host_run_abs_path "$HOST_RUN_PROJECT_ROOT")"
  HOST_RUN_PLATFORM_ROOT="$(host_run_abs_path "$HOST_RUN_PLATFORM_ROOT")"
  if [ -n "$HOST_RUN_LOCAL_PACKAGES_ROOT" ]; then
    HOST_RUN_LOCAL_PACKAGES_ROOT="$(host_run_abs_path "$HOST_RUN_LOCAL_PACKAGES_ROOT")"
  fi
  HOST_RUN_DURABLE_ROOT="$(host_run_abs_path "$HOST_RUN_DURABLE_ROOT")"
  if [ -n "$HOST_RUN_RUNTIME_BASE" ]; then
    HOST_RUN_RUNTIME_BASE="$(host_run_abs_path "$HOST_RUN_RUNTIME_BASE")"
  else
    HOST_RUN_RUNTIME_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fkst-host-run-rt.XXXXXX")"
    HOST_RUN_RUNTIME_LABEL="fresh temp"
  fi
}

host_run_validate_shape() {
  [ -d "$HOST_RUN_PROJECT_ROOT" ] || { echo "error: project root does not exist: $HOST_RUN_PROJECT_ROOT" >&2; return 1; }
  local work_tree_result core_bare
  if ! work_tree_result="$(git -C "$HOST_RUN_PLATFORM_ROOT" rev-parse --is-inside-work-tree)" || [ "$work_tree_result" != "true" ]; then
    core_bare="$(git -C "$HOST_RUN_PLATFORM_ROOT" config --bool core.bare 2>/dev/null || true)"
    printf 'error: --platform-root failed predicate git rev-parse --is-inside-work-tree=true: %s' "$HOST_RUN_PLATFORM_ROOT" >&2
    if [ "$core_bare" = "true" ]; then
      printf ' (core.bare=true)' >&2
    fi
    if [ -n "$work_tree_result" ]; then
      printf ': %s' "$work_tree_result" >&2
    fi
    printf '\n' >&2
    return 1
  fi
  mkdir -p "$HOST_RUN_DURABLE_ROOT"
  if [ "$HOST_RUN_RUNTIME_IS_EXPLICIT" -eq 1 ]; then
    mkdir -p "$HOST_RUN_RUNTIME_BASE"
    HOST_RUN_RUNTIME_ROOT="$HOST_RUN_RUNTIME_BASE"
    HOST_RUN_RUNTIME_LABEL="explicit"
    if host_run_same_path "$HOST_RUN_RUNTIME_ROOT" "$HOST_RUN_DURABLE_ROOT"; then
      echo "error: --runtime-root and --durable-root resolved to the same directory" >&2
      return 1
    fi
  fi
  if host_run_same_path "$HOST_RUN_RUNTIME_ROOT" "$HOST_RUN_DURABLE_ROOT"; then
    echo "error: --runtime-root and --durable-root resolved to the same directory" >&2
    return 1
  fi
}

host_run_validate_local_iteration_test_command_for() {
  local project_root="$1" package_names="$2" name command executable candidate
  local needs_local_gate=0
  for name in $package_names; do
    case "$name" in
      github-devloop|github-devloop-pr) needs_local_gate=1 ;;
    esac
  done
  [ "$needs_local_gate" -eq 1 ] || return 0

  command="${FKST_DEVLOOP_LOCAL_TEST_COMMAND:-scripts/run.sh test-affected}"
  command="${command#"${command%%[![:space:]]*}"}"
  command="${command%"${command##*[![:space:]]}"}"
  case "$command" in
    ""|*$'\n'*|*';'*|*'&'*|*'|'*|*'<'*|*'>'*|*'`'*|*'$('* )
      echo "error: FKST_DEVLOOP_LOCAL_TEST_COMMAND must be one executable invocation; put multi-step logic in a repository-owned executable or task target" >&2
      return 1
      ;;
  esac
  if ! /bin/bash -n -c "$command" >/dev/null 2>&1; then
    echo "error: FKST_DEVLOOP_LOCAL_TEST_COMMAND has invalid shell syntax" >&2
    return 1
  fi

  executable="${command%%[[:space:]]*}"
  case "$executable" in
    ""|*[!A-Za-z0-9_./+-]*)
      echo "error: FKST_DEVLOOP_LOCAL_TEST_COMMAND must start with an unquoted executable name or path" >&2
      return 1
      ;;
    /*) candidate="$executable" ;;
    */*) candidate="$project_root/$executable" ;;
    *)
      if ! (cd "$project_root" && command -v "$executable" >/dev/null 2>&1); then
        echo "error: local iteration test command is not runnable from $project_root: $command; set FKST_DEVLOOP_LOCAL_TEST_COMMAND to this repository's local CI-equivalent gate" >&2
        return 1
      fi
      candidate=""
      ;;
  esac
  if [ -n "$candidate" ] && { [ ! -f "$candidate" ] || [ ! -x "$candidate" ]; }; then
    echo "error: local iteration test command is not runnable from $project_root: $command; set FKST_DEVLOOP_LOCAL_TEST_COMMAND to this repository's local CI-equivalent gate" >&2
    return 1
  fi

  export FKST_DEVLOOP_LOCAL_TEST_COMMAND="$command"
  echo "local_iteration_test_command=$command"
}

host_run_validate_local_iteration_test_command() {
  host_run_validate_local_iteration_test_command_for \
    "$HOST_RUN_PROJECT_ROOT" \
    "$HOST_RUN_PLATFORM_PACKAGES $HOST_RUN_HOST_PACKAGES"
}

host_run_host_package_base() {
  if host_run_same_path "$HOST_RUN_PROJECT_ROOT" "$HOST_RUN_PLATFORM_ROOT"; then
    printf '%s/packages\n' "$HOST_RUN_PROJECT_ROOT"
    return 0
  fi
  if [ -n "$HOST_RUN_LOCAL_PACKAGES_ROOT" ]; then
    printf '%s\n' "$HOST_RUN_LOCAL_PACKAGES_ROOT"
    return 0
  fi
  printf '%s/.fkst/local-packages\n' "$HOST_RUN_PROJECT_ROOT"
}

host_run_add_named_roots() {
  local base="$1" kind="$2" names="$3" name path
  for name in $names; do
    path="$base/$name"
    [ -d "$path" ] || { echo "error: missing $kind package '$name' at $path" >&2; return 1; }
    HOST_RUN_PACKAGE_ROOTS+=("$path")
  done
}

host_run_build_package_roots() {
  HOST_RUN_PACKAGE_ROOTS=()
  host_run_resolve_target_platform_roots || return 1
  if [ -n "$HOST_RUN_HOST_PACKAGES" ]; then
    host_run_add_named_roots "$(host_run_host_package_base)" "host" "$HOST_RUN_HOST_PACKAGES" || return 1
  fi
}

host_run_pid_file() {
  printf '%s/.fkst-supervise.pid\n' "$HOST_RUN_DURABLE_ROOT"
}

host_run_pid_check() {
  local pid="$1" err
  err="$(kill -0 "$pid" 2>&1)" && return 0
  case "$err" in
    *"Operation not permitted"*|*"operation not permitted"*|*"not permitted"*)
      return 2
      ;;
  esac
  return 1
}

host_run_pid_state() {
  local pid="$1" stat
  if [ -r "/proc/$pid/stat" ]; then
    stat="$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $1}')" || stat=""
    [ -n "$stat" ] && { printf '%s\n' "$stat"; return 0; }
  fi
  stat="$(ps -o stat= -p "$pid" 2>/dev/null | awk 'NF {print $1; exit}')" || stat=""
  [ -n "$stat" ] && { printf '%s\n' "$stat"; return 0; }
  return 1
}

host_run_pid_is_dead() {
  local pid="$1" state
  host_run_pid_check "$pid"
  case "$?" in
    0) ;;
    1) return 0 ;;
    *) return 1 ;;
  esac
  state="$(host_run_pid_state "$pid" 2>/dev/null || true)"
  [[ "$state" == Z* ]]
}

host_run_kill_supervise_pid() {
  local pid="$1" pid_file="$2" attempts=0
  if host_run_pid_is_dead "$pid"; then
    echo "restart: removing stale supervise pidfile for dead pid $pid at $pid_file" >&2
    rm -f "$pid_file"
    return 0
  fi
  echo "restart: killing prior supervise pid $pid for durable root $HOST_RUN_DURABLE_ROOT" >&2
  if ! kill -9 "$pid" 2>/dev/null; then
    echo "error: failed to SIGKILL prior supervise pid $pid from $pid_file; refusing to launch a second supervise on $HOST_RUN_DURABLE_ROOT" >&2
    return 1
  fi
  while [ "$attempts" -lt 50 ]; do
    if host_run_pid_is_dead "$pid"; then
      rm -f "$pid_file"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 0.1
  done
  echo "error: prior supervise pid $pid from $pid_file is still alive after SIGKILL; refusing to launch a second supervise on $HOST_RUN_DURABLE_ROOT" >&2
  return 1
}

host_run_restart_prior() {
  local pid_file pid
  [ "$HOST_RUN_RESTART" -eq 1 ] || return 0
  pid_file="$(host_run_pid_file)"
  [ -f "$pid_file" ] || return 0
  pid="$(sed -n '1p' "$pid_file" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*)
      echo "error: malformed supervise pidfile at $pid_file; refusing to launch a second supervise on $HOST_RUN_DURABLE_ROOT" >&2
      return 1
      ;;
    *)
      host_run_kill_supervise_pid "$pid" "$pid_file"
      ;;
  esac
}

host_run_require_engine_binary() {
  [ -f "$BIN" ] && [ -x "$BIN" ] && return 0
  printf 'ENGINE_BINARY_UNAVAILABLE: declared build path: %s\n' "$BIN" >&2
  return 1
}

host_run_claim_supervise_slot() {
  local pid_file pid wrote=0
  pid_file="$(host_run_pid_file)"
  if [ -f "$pid_file" ]; then
    pid="$(sed -n '1p' "$pid_file" 2>/dev/null || true)"
    case "$pid" in
      ''|*[!0-9]*)
        echo "error: malformed supervise pidfile at $pid_file; use --restart after fixing the pidfile" >&2
        return 1
        ;;
      *)
        if ! host_run_pid_is_dead "$pid"; then
          echo "error: supervise pid $pid from $pid_file is still running for durable root $HOST_RUN_DURABLE_ROOT; use --restart to replace it" >&2
          return 1
        fi
        rm -f "$pid_file"
        ;;
    esac
  fi
  if ( set -C; printf '%s\n' "$$" > "$pid_file" ) 2>/dev/null; then
    wrote=1
  fi
  if [ "$wrote" -eq 1 ]; then
    return 0
  fi
  pid="$(sed -n '1p' "$pid_file" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*)
      echo "error: could not claim supervise pidfile at $pid_file" >&2
      ;;
    *)
      echo "error: supervise pid $pid claimed durable root $HOST_RUN_DURABLE_ROOT before launch; use --restart to replace it" >&2
      ;;
  esac
  return 1
}

host_run_print_package_roots() {
  local root
  for root in "${HOST_RUN_PACKAGE_ROOTS[@]}"; do
    printf '%s\n' "$root"
  done
}

host_run_supervise_contract() {
  host_run_parse_supervise_args "$@" || return $?
  host_run_validate_shape || return $?
  host_run_build_package_roots || return $?
  if [ -n "${FKST_RATE_POOL_ROOT:-}" ]; then
    case "$FKST_RATE_POOL_ROOT" in
      /*) ;;
      *)
        echo "error: FKST_RATE_POOL_ROOT must be an absolute host-stable directory path" >&2
        return 1
        ;;
    esac
  fi

  host_run_validate_local_iteration_test_command || return $?
  host_run_require_engine_binary || return $?
  host_run_restart_prior || return $?
  export FKST_RUNTIME_ROOT="$HOST_RUN_RUNTIME_ROOT"
  export FKST_DURABLE_ROOT="$HOST_RUN_DURABLE_ROOT"
  export FKST_PROJECT_ROOT="$HOST_RUN_PROJECT_ROOT"
  local repository_roots=("$HOST_RUN_PROJECT_ROOT" "$HOST_RUN_PLATFORM_ROOT")
  if [ -n "${BIN_REPOSITORY_ROOT:-}" ]; then
    repository_roots+=("$BIN_REPOSITORY_ROOT")
  fi
  host_run_export_codex_repository_roots "${repository_roots[@]}" || return $?

  local args=() rootdir
  args=("$BIN" supervise --project-root "$HOST_RUN_PROJECT_ROOT")
  for rootdir in "${HOST_RUN_PACKAGE_ROOTS[@]}"; do
    args+=(--package-root "$rootdir")
  done
  args+=(--framework-bin "$BIN")

  echo "BIN=$BIN"
  echo "FKST_RUNTIME_ROOT=$FKST_RUNTIME_ROOT${HOST_RUN_RUNTIME_LABEL:+ ($HOST_RUN_RUNTIME_LABEL)}"
  echo "FKST_DURABLE_ROOT=$FKST_DURABLE_ROOT"
  if [ -n "${FKST_RATE_POOL_ROOT:-}" ]; then echo "FKST_RATE_POOL_ROOT=$FKST_RATE_POOL_ROOT"; fi
  if [ -n "${FKST_GITHUB_WRITE:-}" ]; then echo "FKST_GITHUB_WRITE=$FKST_GITHUB_WRITE"; else echo "FKST_GITHUB_WRITE=<unset> (dry-run)"; fi
  echo "project_root=$HOST_RUN_PROJECT_ROOT"
  echo "platform_root=$HOST_RUN_PLATFORM_ROOT"
  echo "package_roots:"
  host_run_print_package_roots | sed 's/^/  /'
  echo "This starts the real supervise event loop in the foreground. Press Ctrl-C to stop."
  echo "exec: ${args[*]}"
  host_run_require_engine_binary || return $?
  host_run_claim_supervise_slot || return $?
  exec "${args[@]}"
}
