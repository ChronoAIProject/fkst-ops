#!/usr/bin/env bash
# Bootstrap fkst-framework from the pinned fkst-substrate source only after all
# ordinary BIN sources miss.

FKST_OPS_HOST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bootstrap_die() {
  echo "error: $*" >&2
  exit 1
}

bootstrap_cache_root() {
  if [ -n "${FKST_BIN_CACHE_ROOT:-}" ]; then
    printf '%s\n' "$FKST_BIN_CACHE_ROOT"
    return 0
  fi
  if [ -n "${XDG_CACHE_HOME:-}" ]; then
    printf '%s/fkst\n' "$XDG_CACHE_HOME"
    return 0
  fi
  if [ -n "${HOME:-}" ]; then
    printf '%s/.cache/fkst\n' "$HOME"
    return 0
  fi
  bootstrap_die "cannot determine fkst-framework cache root; set FKST_BIN_CACHE_ROOT"
}

bootstrap_read_pin() {
  local repo_root="$1" pin_file="$repo_root/.fkst/substrate-ref" pin
  [ -f "$pin_file" ] || bootstrap_die "missing fkst-substrate source pin: $pin_file"
  pin="$(sed -n '1p' "$pin_file")"
  pin="${pin%%#*}"
  pin="${pin#"${pin%%[![:space:]]*}"}"
  pin="${pin%"${pin##*[![:space:]]}"}"
  [ -n "$pin" ] || bootstrap_die "empty fkst-substrate source pin: $pin_file"
  printf '%s\n' "$pin"
}

bootstrap_parse_pin() {
  local pin="$1" owner repo ref owner_repo
  if [[ "$pin" == *@* && "$pin" == */* ]]; then
    owner_repo="${pin%@*}"
    ref="${pin#*@}"
    owner="${owner_repo%%/*}"
    repo="${owner_repo#*/}"
  else
    owner="${FKST_SUBSTRATE_OWNER:-ChronoAIProject}"
    repo="${FKST_SUBSTRATE_REPO_NAME:-fkst-substrate}"
    ref="$pin"
  fi
  [ -n "$owner" ] || bootstrap_die "invalid fkst-substrate pin owner: $pin"
  [ -n "$repo" ] || bootstrap_die "invalid fkst-substrate pin repo: $pin"
  [ -n "$ref" ] || bootstrap_die "invalid fkst-substrate pin ref: $pin"
  printf '%s\n%s\n%s\n' "$owner" "$repo" "$ref"
}

bootstrap_cache_bin_path() {
  local repo_root="$1" cache_root="$2" owner="$3" repo="$4" ref="$5"
  python3 -B "$FKST_OPS_HOST_ROOT/bin_cache.py" "$cache_root" "$owner" "$repo" "$ref"
}

resolve_bin_contract() {
  local repo_root="$1" mode="${2:-bootstrap}" candidate="" pin owner repo ref cache_root cache_bin
  RESOLVED_BIN=""
  RESOLVE_BIN_ERROR=""

  if [ -n "${BIN:-}" ]; then
    if [ ! -x "$BIN" ]; then
      RESOLVE_BIN_ERROR="explicit BIN is not executable: $BIN"
      return 1
    fi
    RESOLVED_BIN="$BIN"
    return 0
  fi

  if [ -f "$repo_root/.fkst/env" ]; then
    # `|| true`: no BIN= line is fine under set -o pipefail. Strip optional
    # surrounding quotes and a trailing ` # comment`.
    candidate="$(grep -E '^BIN=' "$repo_root/.fkst/env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    candidate="${candidate%%[[:space:]]#*}"
    candidate="${candidate%\"}"; candidate="${candidate#\"}"; candidate="${candidate%\'}"; candidate="${candidate#\'}"
    if [ -n "$candidate" ]; then
      if [ ! -x "$candidate" ]; then
        RESOLVE_BIN_ERROR=".fkst/env BIN is not executable: $candidate"
        return 1
      fi
      RESOLVED_BIN="$candidate"
      return 0
    fi
  fi

  if command -v fkst-framework >/dev/null 2>&1; then
    RESOLVED_BIN="$(command -v fkst-framework)"
    return 0
  fi

  candidate="$repo_root/../fkst-substrate/target/debug/fkst-framework"
  if [ -x "$candidate" ]; then
    RESOLVED_BIN="$candidate"
    return 0
  fi

  if [ "$mode" = "readonly" ]; then
    if [ -z "${FKST_NO_AUTOBUILD:-}" ] && [ -f "$repo_root/.fkst/substrate-ref" ]; then
      pin="$(bootstrap_read_pin "$repo_root" 2>/dev/null || true)"
      if [ -n "$pin" ]; then
        {
          IFS= read -r owner
          IFS= read -r repo
          IFS= read -r ref
        } < <(bootstrap_parse_pin "$pin" 2>/dev/null || true)
        cache_root="$(bootstrap_cache_root 2>/dev/null || true)"
        if [ -n "${owner:-}" ] && [ -n "${repo:-}" ] && [ -n "${ref:-}" ] && [ -n "$cache_root" ]; then
          cache_bin="$(bootstrap_cache_bin_path "$repo_root" "$cache_root" "$owner" "$repo" "$ref" 2>/dev/null || true)"
          if [ -x "$cache_bin" ]; then
            RESOLVED_BIN="$cache_bin"
            return 0
          fi
        fi
      fi
    fi
    RESOLVE_BIN_ERROR="set BIN to an executable fkst-framework, put fkst-framework on PATH, build ../fkst-substrate, or run scripts/run.sh build"
    return 1
  fi

  if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
    RESOLVE_BIN_ERROR="fkst-framework binary is not executable in CI: ${BIN:-<unset>}"
    return 1
  fi

  echo "fkst-framework binary not found in \$BIN, .fkst/env, PATH, or ../fkst-substrate; bootstrapping pinned source" >&2
  RESOLVED_BIN="$(bootstrap_bin_on_total_miss "$repo_root")" || return $?
  return 0
}

bootstrap_with_lock() {
  local lock_dir="$1" timeout="${FKST_BIN_BOOTSTRAP_LOCK_TIMEOUT:-600}" waited=0
  while ! mkdir "$lock_dir" 2>/dev/null; do
    if [ "$waited" -ge "$timeout" ]; then
      bootstrap_die "timed out waiting for fkst-framework bootstrap lock: $lock_dir"
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

bootstrap_checkout_ref() {
  local checkout_dir="$1" ref="$2"
  if git -C "$checkout_dir" checkout --detach "$ref" 1>&2; then
    return 0
  fi
  git -C "$checkout_dir" checkout --detach "origin/$ref" 1>&2
}

bootstrap_bin_on_total_miss() {
  local repo_root="$1" pin owner repo ref cache_root bin_path checkout_dir parent_dir lock_dir repo_url

  if [ -n "${FKST_NO_AUTOBUILD:-}" ]; then
    echo "error: fkst-framework binary not found and FKST_NO_AUTOBUILD is set; refusing network clone or build" >&2
    echo "  fix: set BIN to an executable fkst-framework, put fkst-framework on PATH, build ../fkst-substrate, or unset FKST_NO_AUTOBUILD" >&2
    exit 1
  fi
  command -v git >/dev/null 2>&1 || bootstrap_die "required tool missing for fkst-framework bootstrap: git"
  command -v cargo >/dev/null 2>&1 || bootstrap_die "required tool missing for fkst-framework bootstrap: cargo"
  command -v python3 >/dev/null 2>&1 || bootstrap_die "required tool missing for fkst-framework bootstrap: python3"

  pin="$(bootstrap_read_pin "$repo_root")"
  {
    IFS= read -r owner
    IFS= read -r repo
    IFS= read -r ref
  } < <(bootstrap_parse_pin "$pin")
  cache_root="$(bootstrap_cache_root)"
  bin_path="$(bootstrap_cache_bin_path "$repo_root" "$cache_root" "$owner" "$repo" "$ref")"
  checkout_dir="${bin_path%/target/debug/fkst-framework}"
  parent_dir="$(dirname "$checkout_dir")"
  mkdir -p "$parent_dir"
  lock_dir="$checkout_dir.lock"

  bootstrap_with_lock "$lock_dir"
  if (
    repo_url="https://github.com/$owner/$repo.git"
    if [ -d "$checkout_dir/.git" ]; then
      git -C "$checkout_dir" fetch --tags origin '+refs/heads/*:refs/remotes/origin/*' 1>&2 || exit $?
    else
      rm -rf "$checkout_dir"
      git clone --no-checkout "$repo_url" "$checkout_dir" 1>&2 || exit $?
      git -C "$checkout_dir" fetch --tags origin '+refs/heads/*:refs/remotes/origin/*' 1>&2 || exit $?
    fi

    bootstrap_checkout_ref "$checkout_dir" "$ref" || exit $?
    cargo build --manifest-path "$checkout_dir/Cargo.toml" -p fkst-framework 1>&2 || exit $?
    [ -x "$bin_path" ] || bootstrap_die "fkst-framework bootstrap did not produce an executable binary: $bin_path"
    printf '%s\n' "$bin_path"
  ); then
    rm -rf "$lock_dir"
  else
    local rc=$?
    rm -rf "$lock_dir"
    return "$rc"
  fi
}
