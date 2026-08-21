#!/usr/bin/env bash
# fkst-framework BIN resolution and local-source freshness for scripts/run.sh.

BIN_REPOSITORY_ROOT=""

resolve_bin() {
  if ! resolve_bin_contract "$ROOT" "bootstrap"; then
    local_iteration_result_fail "TOOLCHAIN"
    echo "error: $RESOLVE_BIN_ERROR" >&2
    if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
      echo "  CI must build the engine source and inject BIN; scripts/run.sh will not build in CI." >&2
    fi
    exit 1
  fi
  BIN="$RESOLVED_BIN"
  export BIN
}

# Resolve a path to its physical location, following file symlinks too (portable:
# no realpath / `readlink -f` dependency, works with macOS BSD readlink).
resolve_phys_path() {
  local p="$1" target dir
  while [ -L "$p" ]; do
    target="$(readlink "$p")" || break
    case "$target" in
      /*) p="$target" ;;
      *)  p="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)/$target" ;;
    esac
  done
  dir="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)" || return 1
  printf '%s/%s\n' "$dir" "$(basename "$p")"
}

warn_if_substrate_behind() {
  local substrate="$1" behind
  behind="$(git -C "$substrate" rev-list --count HEAD..origin/dev 2>/dev/null)" || behind=""
  if [ -n "$behind" ] && [ "$behind" -gt 0 ] 2>/dev/null; then
    echo "warning: engine source checkout '$substrate' is $behind commit(s) behind its origin/dev;" >&2
    echo "         the BIN may be stale (missing newer engine primitives). scripts/run.sh builds from the" >&2
    echo "         CURRENT checkout and does NOT git-pull — run 'git pull' in that checkout, then rebuild to refresh." >&2
  fi
}

ensure_fresh_bin() {
  local phys substrate suffix
  BIN_REPOSITORY_ROOT=""
  suffix="/target/debug/fkst-framework"
  phys="$(resolve_phys_path "$BIN")" || phys="$BIN"
  if [[ "$phys" == *"$suffix" ]]; then
    substrate="${phys%"$suffix"}"
  else
    substrate=""
  fi
  if [ -n "$substrate" ] && [ -d "$substrate/.git" ] && [ -f "$substrate/Cargo.toml" ]; then
    BIN_REPOSITORY_ROOT="$substrate"
  else
    substrate=""
  fi

  if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
    return 0
  fi

  if [ -z "$substrate" ]; then
    if [ -z "${FKST_NO_AUTOBUILD:-}" ]; then
      echo "warning: cannot trace BIN to an engine source checkout; skipping freshness build: $BIN" >&2
    fi
    return 0
  fi

  warn_if_substrate_behind "$substrate"

  if [ -n "${FKST_NO_AUTOBUILD:-}" ]; then
    echo "warning: FKST_NO_AUTOBUILD set; skipping fkst-framework freshness build" >&2
    return 0
  fi

  echo "ensuring fkst-framework is built from current source: $substrate" >&2
  local build_out cargo_bin
  if [ -n "${FKST_CARGO:-}" ]; then
    cargo_bin="$FKST_CARGO"
  else
    cargo_bin="cargo"
    echo "warning: FKST_CARGO is not set; falling back to cargo from PATH for this local freshness build" >&2
  fi
  if ! build_out="$("$cargo_bin" build --manifest-path "$substrate/Cargo.toml" -p fkst-framework 2>&1)"; then
    local_iteration_result_fail "TOOLCHAIN"
    printf '%s\n' "$build_out" >&2
    echo "error: fkst-framework freshness build failed; refusing to continue with a potentially stale BIN" >&2
    exit 1
  fi
}
