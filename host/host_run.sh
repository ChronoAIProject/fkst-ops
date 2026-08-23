#!/usr/bin/env bash
# Host-run contract helpers for scripts/run.sh supervise.

HOST_RUN_PROJECT_ROOT=""
HOST_RUN_PLATFORM_ROOT=""
HOST_RUN_PLATFORM_PACKAGES=""
HOST_RUN_DURABLE_ROOT=""
HOST_RUN_RUNTIME_ROOT=""
HOST_RUN_RUNTIME_BASE=""
HOST_RUN_RUNTIME_LABEL=""
HOST_RUN_RUNTIME_IS_EXPLICIT=0
HOST_RUN_RESTART=0
HOST_RUN_EXPECTED_ENGINE_REVISION=""
HOST_RUN_PACKAGE_ROOTS=()
# Parallel arrays retain each additional source's root and package names without a delimiter that
# could also occur in a checkout path. The caller has already decided each name-to-root binding.
HOST_RUN_PACKAGE_SOURCE_ROOTS=()
HOST_RUN_PACKAGE_SOURCE_NAMES=()

host_run_usage() {
  cat >&2 <<'EOF'
usage: scripts/run.sh supervise --project-root <HOST> --platform-root <PKGSRC> --platform-packages "<names>" --expected-engine-revision <sha> [--package-source <root> "<names>"]... --durable-root <path> [--runtime-root <fresh-scratch-root>] [--restart]
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

host_run_parse_supervise_args() {
  HOST_RUN_PROJECT_ROOT=""
  HOST_RUN_PLATFORM_ROOT=""
  HOST_RUN_PLATFORM_PACKAGES=""
  HOST_RUN_DURABLE_ROOT=""
  HOST_RUN_RUNTIME_ROOT=""
  HOST_RUN_RUNTIME_BASE=""
  HOST_RUN_RUNTIME_LABEL=""
  HOST_RUN_RUNTIME_IS_EXPLICIT=0
  HOST_RUN_RESTART=0
  HOST_RUN_EXPECTED_ENGINE_REVISION=""
  HOST_RUN_PACKAGE_SOURCE_ROOTS=()
  HOST_RUN_PACKAGE_SOURCE_NAMES=()

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --project-root)
        [ "$#" -ge 2 ] || { echo "error: --project-root requires a path" >&2; return 2; }
        HOST_RUN_PROJECT_ROOT="$2"; shift 2 ;;
      --platform-root)
        [ "$#" -ge 2 ] || { echo "error: --platform-root requires a path" >&2; return 2; }
        HOST_RUN_PLATFORM_ROOT="$2"; shift 2 ;;
      --platform-packages)
        [ "$#" -ge 2 ] || { echo "error: --platform-packages requires a package list" >&2; return 2; }
        HOST_RUN_PLATFORM_PACKAGES="$2"; shift 2 ;;
      --expected-engine-revision)
        [ "$#" -ge 2 ] || { echo "error: --expected-engine-revision requires a revision" >&2; return 2; }
        HOST_RUN_EXPECTED_ENGINE_REVISION="$2"; shift 2 ;;
      --package-source)
        [ "$#" -ge 3 ] || { echo "error: --package-source requires <root> <names>" >&2; return 2; }
        case "$2" in
          /*) ;;
          *) echo "error: --package-source root must be absolute: $2" >&2; return 2 ;;
        esac
        [ -n "$3" ] || { echo "error: --package-source names must not be empty: $2" >&2; return 2; }
        # Normalised here, like every other root in this contract: two spellings of one
        # directory would reach the engine as two package roots.
        HOST_RUN_PACKAGE_SOURCE_ROOTS+=("$(host_run_abs_path "$2")")
        HOST_RUN_PACKAGE_SOURCE_NAMES+=("$3")
        shift 3 ;;
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
    "$HOST_RUN_PLATFORM_PACKAGES $(host_run_package_source_names | tr '\n' ' ')"
}

host_run_register_package_names() {
  local LC_ALL=C names="$1" name existing
  for name in $names; do
    case "$name" in
      host)
        echo "error: package name 'host' is reserved" >&2
        return 1
        ;;
      *[!A-Za-z0-9_-]*)
        echo "error: invalid package name '$name': must match [A-Za-z0-9_-]+" >&2
        return 1
        ;;
    esac
    for existing in ${HOST_RUN_PACKAGE_NAMES[@]+"${HOST_RUN_PACKAGE_NAMES[@]}"}; do
      [ "$existing" != "$name" ] || {
        echo "error: duplicate package name '$name' across launch roots" >&2
        return 1
      }
    done
    HOST_RUN_PACKAGE_NAMES+=("$name")
  done
}

host_run_add_named_roots() {
  local base="$1" kind="$2" names="$3" name path base_physical path_physical
  host_run_register_package_names "$names" || return 1
  for name in $names; do
    # Validated names are one path segment, and the physical-parent check also rejects symlinks
    # that would redirect a package root outside the source named by the caller.
    path="$base/$name"
    [ -d "$path" ] || { echo "error: missing $kind package '$name' at $path" >&2; return 1; }
    base_physical="$(cd "$base" 2>/dev/null && pwd -P)" || return 1
    path_physical="$(cd "$path" 2>/dev/null && pwd -P)" || return 1
    [ "${path_physical%/*}" = "$base_physical" ] || {
      echo "error: $kind package '$name' is not a direct child of $base" >&2
      return 1
    }
    HOST_RUN_PACKAGE_ROOTS+=("$path")
  done
}

host_run_build_package_roots() {
  local index
  HOST_RUN_PACKAGE_ROOTS=()
  HOST_RUN_PACKAGE_NAMES=()
  HOST_RUN_PLATFORM_ROOT="$(cd "$HOST_RUN_PLATFORM_ROOT" && pwd -P)" || return 1
  host_run_add_named_roots "$HOST_RUN_PLATFORM_ROOT/packages" \
    "platform" "$HOST_RUN_PLATFORM_PACKAGES" || return 1
  for ((index=0; index<${#HOST_RUN_PACKAGE_SOURCE_ROOTS[@]}; index++)); do
    host_run_add_named_roots "${HOST_RUN_PACKAGE_SOURCE_ROOTS[$index]}/packages" \
      "package source" "${HOST_RUN_PACKAGE_SOURCE_NAMES[$index]}" || return 1
  done
}

# Every root the run loads from, so a caller can grant the same set to a codex worker.
host_run_package_source_roots() {
  local root
  for root in ${HOST_RUN_PACKAGE_SOURCE_ROOTS+"${HOST_RUN_PACKAGE_SOURCE_ROOTS[@]}"}; do
    printf '%s\n' "$root"
  done
}

host_run_package_source_names() {
  local names
  for names in ${HOST_RUN_PACKAGE_SOURCE_NAMES+"${HOST_RUN_PACKAGE_SOURCE_NAMES[@]}"}; do
    printf '%s\n' "$names"
  done
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

# Verify that BIN is the engine revision the operator declared and that its bytes
# still match the receipt published beside it. This is corruption detection, not
# tamper resistance: a writer with access to this tree already owns everything the
# check could protect. It exists because a build killed partway, a truncated copy, a
# full disk or an interrupted publication can leave bytes that do not match their
# receipt, and because the operator's own verification cannot cover the interval
# between its last check and this exec.
#
# The check is self-contained on purpose. It reads only the receipt beside BIN, so it
# does not depend on any file outside this repository.
host_run_require_engine_binary() {
  [ -f "$BIN" ] && [ -x "$BIN" ] && return 0
  printf 'ENGINE_BINARY_UNAVAILABLE: declared build path: %s\n' "$BIN" >&2
  return 1
}

host_run_require_engine_receipt() {
  local tool
  tool="$(cd "$(dirname "${BASH_SOURCE[0]}")/../ops" 2>/dev/null && pwd)/revision_derivation.py"
  [ -f "$tool" ] && python3 "$tool" receipt-bytes-current \
    "$BIN" "$HOST_RUN_EXPECTED_ENGINE_REVISION" && return 0
  printf 'ENGINE_BINARY_RECEIPT_MISMATCH: revision %s bytes at %s do not match its receipt\n' \
    "$HOST_RUN_EXPECTED_ENGINE_REVISION" "$BIN" >&2
  return 1
}

host_run_require_expected_engine_revision() {
  if [[ ! "$HOST_RUN_EXPECTED_ENGINE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ENGINE_REVISION_INVALID: --expected-engine-revision must be a full lowercase Git SHA" >&2
    return 1
  fi
  case "$BIN" in
    *-"$HOST_RUN_EXPECTED_ENGINE_REVISION") ;;
    *)
      printf 'ENGINE_REVISION_MISMATCH: expected %s, binary path is %s\n' \
        "$HOST_RUN_EXPECTED_ENGINE_REVISION" "$BIN" >&2
      return 1
      ;;
  esac
  export FKST_EXPECTED_ENGINE_REVISION="$HOST_RUN_EXPECTED_ENGINE_REVISION"
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
  host_run_require_expected_engine_revision || return $?
  host_run_require_engine_binary || return $?
  host_run_restart_prior || return $?
  export FKST_RUNTIME_ROOT="$HOST_RUN_RUNTIME_ROOT"
  export FKST_DURABLE_ROOT="$HOST_RUN_DURABLE_ROOT"
  export FKST_PROJECT_ROOT="$HOST_RUN_PROJECT_ROOT"
  local repository_roots=("$HOST_RUN_PROJECT_ROOT" "$HOST_RUN_PLATFORM_ROOT")
  local source_root
  while IFS= read -r source_root; do
    [ -n "$source_root" ] && repository_roots+=("$source_root")
  done < <(host_run_package_source_roots)
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
  host_run_require_engine_receipt || {
    local receipt_status=$?
    rm -f "$(host_run_pid_file)"
    return "$receipt_status"
  }
  exec "${args[@]}"
}
