#!/usr/bin/env bash
# Host-repo entrypoint helpers for scripts/run.sh host.

HOST_ENTRY_HOST_ROOT=""
HOST_ENTRY_PLATFORM_ROOT=""
HOST_ENTRY_PLATFORM_SOURCE_ID="${HOST_ENTRY_PLATFORM_SOURCE_ID:-platform}"
HOST_ENTRY_PACKAGE_ROOTS=()
HOST_ENTRY_HOST_PACKAGE_ROOTS=()
HOST_ENTRY_PLATFORM_PACKAGE_ROOTS=()
HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS=()
HOST_ENTRY_PLATFORM_PACKAGE_NAMES=()
HOST_ENTRY_HOST_PACKAGE_NAMES=()

host_entry_engine_args() {
  if [ "${#HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS[@]}" -gt 0 ]; then
    printf '%s\n' "${HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS[@]}"
  fi
}

host_entry_usage() {
  cat >&2 <<'EOF'
usage: scripts/run.sh host --host-root <HOST> [--platform-root <PKGSRC>] -- <check|test|supervise [args]>
EOF
}

host_entry_abs_path() {
  local path="$1"
  case "$path" in
    /*) printf '%s\n' "$path" ;;
    *) printf '%s/%s\n' "$(pwd -P)" "$path" ;;
  esac
}

host_entry_trim() {
  local text="$1"
  text="${text%%#*}"
  text="${text#"${text%%[![:space:]]*}"}"
  text="${text%"${text##*[![:space:]]}"}"
  printf '%s\n' "$text"
}

host_entry_same_path() {
  local left="$1" right="$2" left_phys right_phys
  left_phys="$(cd "$left" 2>/dev/null && pwd -P)" || return 1
  right_phys="$(cd "$right" 2>/dev/null && pwd -P)" || return 1
  [ "$left_phys" = "$right_phys" ]
}

host_entry_parse() {
  HOST_ENTRY_HOST_ROOT=""
  HOST_ENTRY_PLATFORM_ROOT="$ROOT"

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --host-root)
        [ "$#" -ge 2 ] || { echo "error: --host-root requires a path" >&2; return 2; }
        HOST_ENTRY_HOST_ROOT="$2"; shift 2 ;;
      --platform-root)
        [ "$#" -ge 2 ] || { echo "error: --platform-root requires a path" >&2; return 2; }
        HOST_ENTRY_PLATFORM_ROOT="$2"; shift 2 ;;
      --)
        shift
        break ;;
      -h|--help)
        host_entry_usage
        return 2 ;;
      *)
        echo "error: unknown host option: $1" >&2
        host_entry_usage
        return 2 ;;
    esac
  done

  [ -n "$HOST_ENTRY_HOST_ROOT" ] || { echo "error: --host-root is required" >&2; return 2; }
  [ "$#" -gt 0 ] || { echo "error: host command is required after --" >&2; host_entry_usage; return 2; }

  HOST_ENTRY_HOST_ROOT="$(host_entry_abs_path "$HOST_ENTRY_HOST_ROOT")"
  HOST_ENTRY_PLATFORM_ROOT="$(host_entry_abs_path "$HOST_ENTRY_PLATFORM_ROOT")"
  [ -d "$HOST_ENTRY_HOST_ROOT" ] || { echo "error: host root does not exist: $HOST_ENTRY_HOST_ROOT" >&2; return 1; }
  [ -d "$HOST_ENTRY_PLATFORM_ROOT" ] || { echo "error: platform root does not exist: $HOST_ENTRY_PLATFORM_ROOT" >&2; return 1; }
  [ -d "$HOST_ENTRY_PLATFORM_ROOT/packages" ] || { echo "error: platform root has no packages directory: $HOST_ENTRY_PLATFORM_ROOT/packages" >&2; return 1; }

  HOST_ENTRY_COMMAND=("$@")
}

host_entry_package_name_under() {
  local root="$1" base="$2" rel
  case "$root" in
    "$base"/*)
      rel="${root#"$base"/}"
      case "$rel" in
        */*) return 1 ;;
        "") return 1 ;;
        *) printf '%s\n' "$rel"; return 0 ;;
      esac ;;
  esac
  return 1
}

host_entry_add_platform_name() {
  local name="$1" current
  for current in "${HOST_ENTRY_PLATFORM_PACKAGE_NAMES[@]-}"; do
    [ "$current" = "$name" ] && return 0
  done
  HOST_ENTRY_PLATFORM_PACKAGE_NAMES+=("$name")
}

host_entry_add_host_name() {
  local name="$1" current
  for current in "${HOST_ENTRY_HOST_PACKAGE_NAMES[@]-}"; do
    [ "$current" = "$name" ] && return 0
  done
  HOST_ENTRY_HOST_PACKAGE_NAMES+=("$name")
}

host_entry_add_package_root() {
  local root="$1" name
  HOST_ENTRY_PACKAGE_ROOTS+=("$root")
  HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS+=(--package-root "$root")
  if name="$(host_entry_package_name_under "$root" "$HOST_ENTRY_PLATFORM_ROOT/packages")"; then
    HOST_ENTRY_PLATFORM_PACKAGE_ROOTS+=("$root")
    host_entry_add_platform_name "$name"
    return 0
  fi
  if host_entry_same_path "$HOST_ENTRY_HOST_ROOT" "$HOST_ENTRY_PLATFORM_ROOT" \
      && name="$(host_entry_package_name_under "$root" "$HOST_ENTRY_HOST_ROOT/packages")"; then
    HOST_ENTRY_PLATFORM_PACKAGE_ROOTS+=("$root")
    host_entry_add_platform_name "$name"
    return 0
  fi
  if name="$(host_entry_package_name_under "$root" "$HOST_ENTRY_HOST_ROOT/packages")"; then
    HOST_ENTRY_HOST_PACKAGE_ROOTS+=("$root")
    host_entry_add_host_name "$name"
    return 0
  fi
  echo "error: package root is not under host or platform package views: $root" >&2
  return 1
}

host_entry_resolve_root_line() {
  local line="$1" path
  case "$line" in
    "$HOST_ENTRY_PLATFORM_SOURCE_ID":*) path="$HOST_ENTRY_PLATFORM_ROOT/${line#"$HOST_ENTRY_PLATFORM_SOURCE_ID:"}" ;;
    /*) path="$line" ;;
    *) path="$HOST_ENTRY_HOST_ROOT/$line" ;;
  esac
  if [ ! -d "$path" ]; then
    echo "error: conformance package root does not exist: $line -> $path" >&2
    return 1
  fi
  host_entry_add_package_root "$path"
}

host_entry_load_configured_roots() {
  local config="$HOST_ENTRY_HOST_ROOT/.fkst/compose/package-roots" line
  [ -f "$config" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(host_entry_trim "$line")"
    [ -n "$line" ] || continue
    host_entry_resolve_root_line "$line" || return 2
  done < "$config"
  return 0
}

host_entry_discover_roots() {
  local rootdir
  if host_entry_same_path "$HOST_ENTRY_HOST_ROOT" "$HOST_ENTRY_PLATFORM_ROOT"; then
    for rootdir in "$HOST_ENTRY_PLATFORM_ROOT"/packages/*/; do
      [ -d "$rootdir" ] || continue
      host_entry_add_package_root "${rootdir%/}" || return 1
    done
    return 0
  fi
  for rootdir in "$HOST_ENTRY_HOST_ROOT"/packages/*/; do
    [ -d "$rootdir" ] || continue
    host_entry_add_package_root "${rootdir%/}" || return 1
  done
}

host_entry_build_package_roots() {
  HOST_ENTRY_PACKAGE_ROOTS=()
  HOST_ENTRY_HOST_PACKAGE_ROOTS=()
  HOST_ENTRY_PLATFORM_PACKAGE_ROOTS=()
  HOST_ENTRY_ENGINE_PACKAGE_ROOT_ARGS=()
  HOST_ENTRY_PLATFORM_PACKAGE_NAMES=()
  HOST_ENTRY_HOST_PACKAGE_NAMES=()

  local status
  set +e
  host_entry_load_configured_roots
  status=$?
  set -e
  case "$status" in
    0) ;;
    1) host_entry_discover_roots ;;
    *) return "$status" ;;
  esac

  if [ "${#HOST_ENTRY_PACKAGE_ROOTS[@]}" -eq 0 ]; then
    echo "error: no host package roots found under $HOST_ENTRY_HOST_ROOT" >&2
    return 1
  fi
}

host_entry_source_ratchet_args() {
  HOST_ENTRY_SOURCE_RATCHET_ARGS=(--project-root "$HOST_ENTRY_HOST_ROOT" --platform-root "$HOST_ENTRY_PLATFORM_ROOT")
  if [ -d "$HOST_ENTRY_HOST_ROOT/.fkst/conformance/allowlists" ]; then
    HOST_ENTRY_SOURCE_RATCHET_ARGS+=(--allowlist-dir "$HOST_ENTRY_HOST_ROOT/.fkst/conformance/allowlists")
  fi
}

host_entry_run_shared_source_ratchets() {
  host_entry_source_ratchet_args
  echo "=== host source ratchets ==="
  PYTHONPATH="$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -B "$ROOT/scripts/check_repo.py" "${HOST_ENTRY_SOURCE_RATCHET_ARGS[@]}"
}

host_entry_run_engine_conformance() {
  local output_file rc engine_args=() cmd=()
  echo "=== host engine conformance ==="
  output_file="$(mktemp "${TMPDIR:-/tmp}/fkst-host-conformance.XXXXXX")"
  while IFS= read -r arg; do
    engine_args+=("$arg")
  done < <(host_entry_engine_args)
  cmd=("$BIN" conformance --project-root "$HOST_ENTRY_HOST_ROOT")
  if [ "${#engine_args[@]}" -gt 0 ]; then
    cmd+=("${engine_args[@]}")
  fi
  set +e
  if [ -n "${verbose:-}${FKST_TEST_VERBOSE:-}" ]; then
    "${cmd[@]}" >"$output_file" 2>&1
    rc=$?
  else
    "${cmd[@]}" 2>&1 | grep -vE '^PASS ' >"$output_file"
    rc=${PIPESTATUS[0]}
  fi
  set -e
  cat "$output_file"
  if [ "$rc" -ne 0 ]; then
    rm -f "$output_file"
    return "$rc"
  fi
  set +e
  python3 - "$output_file" <<'PY'
import json
import sys

verdict = None
with open(sys.argv[1], encoding="utf-8") as handle:
    for raw in handle:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "ok" in payload:
            verdict = payload.get("ok")

if verdict is True:
    sys.exit(0)
if verdict is False:
    print("error: conformance reported ok=false", file=sys.stderr)
else:
    print("error: conformance did not emit a JSON ok verdict", file=sys.stderr)
sys.exit(1)
PY
  rc=$?
  set -e
  rm -f "$output_file"
  return "$rc"
}

host_entry_package_test_project_root() {
  local pkg="$1"
  if host_entry_same_path "$HOST_ENTRY_HOST_ROOT" "$HOST_ENTRY_PLATFORM_ROOT" \
      && host_entry_package_name_under "$pkg" "$HOST_ENTRY_PLATFORM_ROOT/packages" >/dev/null; then
    printf '%s\n' "$pkg"
    return 0
  fi
  printf '%s\n' "$HOST_ENTRY_HOST_ROOT"
}

host_entry_package_has_run_graph_tests() {
  local pkg="$1"
  compgen -G "$pkg/tests/run_graph*_test.lua" >/dev/null
}

host_entry_package_has_non_run_graph_tests() {
  local pkg="$1" path base
  for path in "$pkg"/tests/*_test.lua; do
    [ -f "$path" ] || continue
    base="$(basename "$path")"
    case "$base" in
      run_graph*_test.lua) ;;
      *) return 0 ;;
    esac
  done
  for path in "$pkg"/departments/*/*_test.lua; do
    [ -f "$path" ] || continue
    return 0
  done
  return 1
}

host_entry_copy_test_package() {
  local src="$1" dest="$2" role="$3"
  mkdir -p "$dest"
  case "$role" in
    normal)
      (cd "$src" && LC_ALL=C tar --exclude './tests/run_graph*_test.lua' -cf - .) \
        | (cd "$dest" && LC_ALL=C tar xf -)
      ;;
    graph-target)
      (cd "$src" && LC_ALL=C tar --exclude './departments/test_*' --exclude './tests/*_test.lua' -cf - .) \
        | (cd "$dest" && LC_ALL=C tar xf -)
      if compgen -G "$src/tests/run_graph*_test.lua" >/dev/null; then
        mkdir -p "$dest/tests"
        cp "$src"/tests/run_graph*_test.lua "$dest/tests/"
      fi
      ;;
    graph-dep)
      (cd "$src" && LC_ALL=C tar --exclude './departments/test_*' --exclude './tests' -cf - .) \
        | (cd "$dest" && LC_ALL=C tar xf -)
      ;;
    *)
      echo "error: unknown host test package copy role: $role" >&2
      return 2
      ;;
  esac
}

host_entry_copy_test_libraries_from() {
  local source_root="$1" work="$2" lib dest
  [ -d "$source_root/libraries" ] || return 0
  mkdir -p "$work/libraries"
  for lib in "$source_root"/libraries/*; do
    [ -d "$lib" ] || continue
    dest="$work/libraries/$(basename "$lib")"
    [ -e "$dest" ] && continue
    mkdir -p "$dest"
    (cd "$lib" && LC_ALL=C tar -cf - .) | (cd "$dest" && LC_ALL=C tar xf -)
  done
}

host_entry_prepare_test_workspace() {
  local work="$1"
  mkdir -p "$work/libraries"
  host_entry_copy_test_libraries_from "$HOST_ENTRY_HOST_ROOT" "$work"
  if ! host_entry_same_path "$HOST_ENTRY_HOST_ROOT" "$HOST_ENTRY_PLATFORM_ROOT"; then
    host_entry_copy_test_libraries_from "$HOST_ENTRY_PLATFORM_ROOT" "$work"
  fi
  cat > "$work/fkst.workspace.toml" <<'TOML'
[workspace]
units = ["packages/*", "libraries/*"]
packages = ["packages/*"]
libraries = ["libraries/*"]

[registries]
workspace = "workspace"
TOML
}

host_entry_prepare_normal_test_root() {
  local target_pkg="$1" work="$2" name dest
  name="$(basename "$target_pkg")"
  dest="$work/packages/$name"
  mkdir -p "$work/packages"
  host_entry_copy_test_package "$target_pkg" "$dest" normal || return 1
  host_entry_prepare_test_workspace "$work" || return 1
  HOST_ENTRY_NORMAL_TEST_ROOT="$dest"
}

host_entry_prepare_run_graph_test_roots() {
  local target_pkg="$1" work="$2" root name dest
  HOST_ENTRY_GRAPH_TEST_ROOTS=()
  mkdir -p "$work/packages"
  for root in "${HOST_ENTRY_PACKAGE_ROOTS[@]}"; do
    name="$(basename "$root")"
    dest="$work/packages/$name"
    if [ -e "$dest" ]; then
      echo "error: duplicate host graph package root name: $name" >&2
      return 1
    fi
    if [ "$root" = "$target_pkg" ]; then
      host_entry_copy_test_package "$root" "$dest" graph-target || return 1
    else
      host_entry_copy_test_package "$root" "$dest" graph-dep || return 1
    fi
    HOST_ENTRY_GRAPH_TEST_ROOTS+=("$dest")
  done
  host_entry_prepare_test_workspace "$work" || return 1
}

host_entry_cmd_check() {
  host_entry_build_package_roots
  host_entry_run_shared_source_ratchets
  resolve_bin
  ensure_fresh_bin
  host_entry_run_engine_conformance
}

host_entry_cmd_test() {
  local target="" pkg name project_root ran=0 fail=0 report_dir report_file conf_cmd=() test_cmd=() test_roots=() rc
  local normal_pkg normal_project_root normal_work graph_work graph_root has_run_graph run_normal
  local -a graph_args
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -v|--verbose) FKST_TEST_VERBOSE=1; export FKST_TEST_VERBOSE ;;
      -*) echo "unknown host test flag: $1" >&2; return 2 ;;
      *) target="$1" ;;
    esac
    shift
  done

  host_entry_build_package_roots
  resolve_bin
  ensure_fresh_bin

  # This EXIT trap overrides the disarm trap cmd_host set when it armed the watchdog, so it must also
  # disarm — else on the host-test path the watchdog is left armed and its orphaned sleep fires a stale
  # kill -9 -pgid later. disarm is idempotent; `|| true` no-ops when test_deadline.sh was not sourced.
  trap 'rm -rf "${HOST_TEST_RUNTIME_ROOT:-}" "${HOST_TEST_DURABLE_ROOT:-}"; disarm_test_deadline 2>/dev/null || true' EXIT
  HOST_TEST_RUNTIME_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fkst-host-test-rt.XXXXXX")"
  HOST_TEST_DURABLE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fkst-host-test-durable.XXXXXX")"
  export FKST_RUNTIME_ROOT="$HOST_TEST_RUNTIME_ROOT"
  export FKST_DURABLE_ROOT="$HOST_TEST_DURABLE_ROOT"
  unset FKST_GITHUB_WRITE
  unset FKST_SUPERVISOR_PID
  report_dir="$(mktemp -d "${TMPDIR:-/tmp}/fkst-host-test-reports.XXXXXX")"

  echo "host test hermetic: FKST_RUNTIME_ROOT=$FKST_RUNTIME_ROOT FKST_DURABLE_ROOT=$FKST_DURABLE_ROOT"
  echo "=== self-test ==="
  if ! run_self_test_with_optional_lua_coverage; then fail=$((fail + 1)); fi

  if [ -z "$target" ]; then
    if ! host_entry_run_engine_conformance; then fail=$((fail + 1)); fi
  fi

  test_roots=()
  if [ "${#HOST_ENTRY_HOST_PACKAGE_ROOTS[@]}" -gt 0 ]; then
    test_roots+=("${HOST_ENTRY_HOST_PACKAGE_ROOTS[@]}")
  fi
  if host_entry_same_path "$HOST_ENTRY_HOST_ROOT" "$HOST_ENTRY_PLATFORM_ROOT"; then
    if [ "${#HOST_ENTRY_PLATFORM_PACKAGE_ROOTS[@]}" -gt 0 ]; then
      test_roots+=("${HOST_ENTRY_PLATFORM_PACKAGE_ROOTS[@]}")
    fi
  fi

  if [ "${#test_roots[@]}" -gt 0 ]; then
    for pkg in "${test_roots[@]}"; do
      name="$(basename "$pkg")"
      if [ -n "$target" ] && [ "$name" != "$target" ]; then continue; fi
      echo "=== $name ==="
      ran=$((ran + 1))
      project_root="$(host_entry_package_test_project_root "$pkg")"
      rc=0
      is_composed "$pkg" || rc=$?
      case "$rc" in
        0)
          echo "skip single-package conformance for composed package: $name"
          ;;
        1)
          conf_cmd=("$BIN" conformance --project-root "$project_root" --package-root "$pkg")
          if ! run_quiet_pass "${conf_cmd[@]}"; then
            fail=$((fail + 1))
            continue
          fi
          ;;
        *)
          echo "error: failed to read package composition for $pkg" >&2
          fail=$((fail + 1))
          continue
          ;;
      esac
      report_file="$report_dir/$name.json"
      has_run_graph=0
      run_normal=1
      normal_pkg="$pkg"
      normal_project_root="$project_root"
      normal_work=""
      if host_entry_package_has_run_graph_tests "$pkg"; then
        has_run_graph=1
        if host_entry_package_has_non_run_graph_tests "$pkg"; then
          normal_work="$(mktemp -d "$HOST_TEST_RUNTIME_ROOT/host-test-normal-$name.XXXXXX")" || {
            echo "error: could not create host normal-test staging root for $name" >&2
            fail=$((fail + 1))
            continue
          }
          if ! host_entry_prepare_normal_test_root "$pkg" "$normal_work"; then
            fail=$((fail + 1))
            continue
          fi
          normal_pkg="$HOST_ENTRY_NORMAL_TEST_ROOT"
          normal_project_root="$normal_work"
        else
          run_normal=0
        fi
      fi
      if [ "$run_normal" -eq 1 ]; then
        test_cmd=("$BIN" test --project-root "$normal_project_root" --package-root "$normal_pkg")
        test_cmd+=(--report-json "$report_file")
        if ! run_quiet_keep '^FAIL |passed, [0-9]+ failed|panic' "${test_cmd[@]}"; then
          fail=$((fail + 1))
          continue
        fi
      fi
      if [ "$has_run_graph" -eq 1 ]; then
        graph_work="$(mktemp -d "$HOST_TEST_RUNTIME_ROOT/host-test-graph-$name.XXXXXX")" || {
          echo "error: could not create host graph-test staging root for $name" >&2
          fail=$((fail + 1))
          continue
        }
        if ! host_entry_prepare_run_graph_test_roots "$pkg" "$graph_work"; then
          fail=$((fail + 1))
          continue
        fi
        graph_args=()
        for graph_root in "${HOST_ENTRY_GRAPH_TEST_ROOTS[@]}"; do
          graph_args+=(--package-root "$graph_root")
        done
        test_cmd=("$BIN" test --project-root "$graph_work" "${graph_args[@]}" --report-json "$report_dir/$name.graph.json")
        if ! run_quiet_keep '^FAIL |passed, [0-9]+ failed|panic' "${test_cmd[@]}"; then
          fail=$((fail + 1))
        fi
      fi
    done
  fi

  rm -rf "$report_dir"
  if [ "$ran" -eq 0 ]; then
    if [ -n "$target" ]; then
      echo "no host packages matched for '$target'" >&2
    else
      echo "no host packages matched" >&2
    fi
    return 1
  fi
  if [ "$fail" -ne 0 ]; then
    echo "FAILED: $fail failure(s) across $ran host package(s)" >&2
    return 1
  fi
  echo "OK: $ran host package(s)"
}

host_entry_join_names() {
  local first=1 name
  for name in "$@"; do
    if [ "$first" -eq 1 ]; then
      printf '%s' "$name"
      first=0
    else
      printf ' %s' "$name"
    fi
  done
}

host_entry_cmd_supervise() {
  host_entry_build_package_roots
  resolve_bin
  ensure_fresh_bin

  local platform_names host_names args=()
  platform_names=""
  host_names=""
  if [ "${#HOST_ENTRY_PLATFORM_PACKAGE_NAMES[@]}" -gt 0 ]; then
    platform_names="$(host_entry_join_names "${HOST_ENTRY_PLATFORM_PACKAGE_NAMES[@]}")"
  fi
  if [ "${#HOST_ENTRY_HOST_PACKAGE_NAMES[@]}" -gt 0 ]; then
    host_names="$(host_entry_join_names "${HOST_ENTRY_HOST_PACKAGE_NAMES[@]}")"
  fi
  if [ -z "$platform_names" ]; then
    echo "error: host supervise requires at least one ${HOST_ENTRY_PLATFORM_SOURCE_ID}:<path> package root in .fkst/compose/package-roots" >&2
    return 1
  fi

  if [ -n "$host_names" ]; then
    echo "error: host supervise does not accept target-owned package roots: $host_names" >&2
    return 1
  fi
  args=(--project-root "$HOST_ENTRY_HOST_ROOT" --platform-root "$HOST_ENTRY_PLATFORM_ROOT" --platform-packages "$platform_names")
  args+=("$@")
  host_run_supervise_contract "${args[@]}"
}

cmd_host() {
  host_entry_parse "$@" || return $?
  local subcommand="${HOST_ENTRY_COMMAND[0]}"
  # Bound host-delegated check/test like the top-level test family (see scripts/test_deadline.sh) so a
  # SIGKILLed-parent orphan self-terminates; NOT supervise, which is long-running by design. Guarded by
  # command -v so a host_entry.sh sourced without test_deadline.sh (isolated tests) is a no-op.
  case "$subcommand" in
    check|test)
      if command -v arm_test_deadline >/dev/null 2>&1; then
        arm_test_deadline; trap 'disarm_test_deadline' EXIT
      fi ;;
  esac
  case "$subcommand" in
    check)
      if [ "${#HOST_ENTRY_COMMAND[@]}" -ne 1 ]; then
        echo "usage: scripts/run.sh host --host-root <HOST> -- check" >&2
        return 2
      fi
      host_entry_cmd_check ;;
    test)
      if [ "${#HOST_ENTRY_COMMAND[@]}" -gt 1 ]; then
        host_entry_cmd_test "${HOST_ENTRY_COMMAND[@]:1}"
      else
        host_entry_cmd_test
      fi ;;
    supervise)
      if [ "${#HOST_ENTRY_COMMAND[@]}" -gt 1 ]; then
        host_entry_cmd_supervise "${HOST_ENTRY_COMMAND[@]:1}"
      else
        host_entry_cmd_supervise
      fi ;;
    -h|--help|help)
      host_entry_usage
      return 0 ;;
    *)
      echo "unknown host subcommand: $subcommand" >&2
      host_entry_usage
      return 2 ;;
  esac
}
