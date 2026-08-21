#!/usr/bin/env bash
# Bootstrap fkst-framework from the pinned engine source only after all
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

resolve_bin_validate_candidate() {
  local candidate="$1"
  if [ -f "$candidate" ] && [ -x "$candidate" ]; then
    return 0
  fi
  RESOLVE_BIN_ERROR="ENGINE_BINARY_UNAVAILABLE: declared build path: $candidate"
  return 1
}

bootstrap_read_pin() {
  local repo_root="$1" pin_file pin
  pin_file="$repo_root/.fkst/substrate-ref"
  [ -f "$pin_file" ] || bootstrap_die "missing engine source pin: $pin_file"
  pin="$(sed -n '1p' "$pin_file")"
  pin="${pin%%#*}"
  pin="${pin#"${pin%%[![:space:]]*}"}"
  pin="${pin%"${pin##*[![:space:]]}"}"
  [ -n "$pin" ] || bootstrap_die "empty engine source pin: $pin_file"
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
    owner="${FKST_SUBSTRATE_OWNER:-}"
    repo="${FKST_SUBSTRATE_REPO_NAME:-}"
    ref="$pin"
  fi
  [ -n "$owner" ] || bootstrap_die "invalid engine source pin owner: $pin (set FKST_SUBSTRATE_OWNER for a short pin)"
  [ -n "$repo" ] || bootstrap_die "invalid engine source pin repository: $pin (set FKST_SUBSTRATE_REPO_NAME for a short pin)"
  [ -n "$ref" ] || bootstrap_die "invalid engine source pin revision: $pin"
  printf '%s\n%s\n%s\n' "$owner" "$repo" "$ref"
}

bootstrap_cache_bin_path() {
  local repo_root="$1" cache_root="$2" owner="$3" repo="$4" ref="$5"
  python3 -B "$repo_root/scripts/bin_cache.py" "$cache_root" "$owner" "$repo" "$ref"
}

resolve_bin_contract() {
  local repo_root="$1" mode="${2:-bootstrap}" candidate="" pin owner repo ref cache_root cache_bin response
  RESOLVED_BIN=""
  RESOLVE_BIN_ERROR=""

  if [ -n "${BIN:-}" ]; then
    resolve_bin_validate_candidate "$BIN" || return $?
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
      resolve_bin_validate_candidate "$candidate" || return $?
      RESOLVED_BIN="$candidate"
      return 0
    fi
  fi

  if command -v fkst-framework >/dev/null 2>&1; then
    RESOLVED_BIN="$(command -v fkst-framework)"
    resolve_bin_validate_candidate "$RESOLVED_BIN" || return $?
    return 0
  fi

  if [ -n "${FKST_SUBSTRATE_CHECKOUT:-}" ]; then
    candidate="$FKST_SUBSTRATE_CHECKOUT/target/debug/fkst-framework"
    if [ -f "$candidate" ] && [ -x "$candidate" ]; then
      RESOLVED_BIN="$candidate"
      return 0
    fi
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
          if [ -f "$cache_bin" ] && [ -x "$cache_bin" ]; then
            RESOLVED_BIN="$cache_bin"
            return 0
          fi
        fi
      fi
    fi
    RESOLVE_BIN_ERROR="set BIN to an executable fkst-framework, put fkst-framework on PATH, set FKST_SUBSTRATE_CHECKOUT to a built engine source, or run scripts/run.sh build"
    return 1
  fi

  echo "fkst-framework binary not found in \$BIN, .fkst/env, PATH, or FKST_SUBSTRATE_CHECKOUT; checking pinned source cache" >&2
  if [ -n "${FKST_OPS_ENGINE_PROVIDER:-}" ]; then
    local provider_rc=0
    response="$(bootstrap_bin_on_total_miss_ops "$repo_root")" || provider_rc=$?
    if [ "$provider_rc" -ne 0 ]; then
      RESOLVE_BIN_ERROR="ENGINE_PROVIDER_FAILED: declared engine provider exited with status $provider_rc"
      return "$provider_rc"
    fi
    RESOLVED_BIN="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["binary"])')" || {
      RESOLVED_BIN=""
      RESOLVE_BIN_ERROR="ENGINE_PROVIDER_RESPONSE_INVALID: declared engine provider returned no usable binary"
      return 1
    }
  else
    if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
      RESOLVE_BIN_ERROR="fkst-framework binary is not executable in CI: ${BIN:-<unset>}"
      return 1
    fi
    RESOLVED_BIN="$(bootstrap_bin_on_total_miss "$repo_root")" || return $?
  fi
  if [ -z "$RESOLVED_BIN" ]; then
    RESOLVE_BIN_ERROR="ENGINE_BINARY_UNAVAILABLE: binary resolver returned an empty path"
    return 1
  fi
  resolve_bin_validate_candidate "$RESOLVED_BIN" || return $?
  return 0
}

# Single flight: exactly one process builds the pinned binary; every other
# process reuses the artifact it produces. Returns 0 holding the lock, or
# BOOTSTRAP_LOCK_ARTIFACT_READY when the holder finished and "$bin_path" is
# usable — waiting out the whole timeout and dying would discard a binary that
# already exists.
BOOTSTRAP_LOCK_ARTIFACT_READY=2

bootstrap_with_lock() {
  local lock_dir="$1" bin_path="$2" timeout="${FKST_BIN_BOOTSTRAP_LOCK_TIMEOUT:-600}" waited=0
  while ! mkdir "$lock_dir" 2>/dev/null; do
    if [ -x "$bin_path" ]; then
      return "$BOOTSTRAP_LOCK_ARTIFACT_READY"
    fi
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

bootstrap_set_result() {
  local variable_name="${1:-}" result="$2"
  [ -z "$variable_name" ] || printf -v "$variable_name" '%s' "$result"
}

bootstrap_bin_on_total_miss_ops() {
  # engine-provider-configuration: forward committed binding configuration as typed input.
  : "${FKST_OPS_ENGINE_PROVIDER:?declared engine provider is required}"
  : "${FKST_OPS_ENGINE_CHECKOUT:?resolved engine checkout is required}"
  : "${FKST_OPS_ENGINE_BINARY:?resolved engine binary is required}"
  : "${FKST_OPS_ENGINE_REVISION_CHECKOUT:?engine revision derivation checkout is required}"
  : "${FKST_OPS_ENGINE_REVISION_PATH:?engine revision derivation path is required}"
  : "${FKST_OPS_ENGINE_CONFIGURATION:?resolved engine provider configuration is required}"
  local pair platform_revision engine_revision binary response source_revision
  pair=$(python3 "$FKST_OPS_HOST_ROOT/../ops/revision_derivation.py" resolve \
    "$FKST_OPS_ENGINE_REVISION_CHECKOUT" "$FKST_OPS_ENGINE_REVISION_PATH") || return $?
  IFS=$'\t' read -r platform_revision engine_revision <<<"$pair"
  binary="${FKST_OPS_ENGINE_BINARY}-${engine_revision}"
  response=$(python3 -c 'import json,sys; c=json.loads(sys.argv[4]); print(json.dumps({"engine_checkout":sys.argv[1],"engine_binary":sys.argv[2],"expected_revision":sys.argv[3],"operation":"build","build_command":c["build_command"]}))' \
    "$FKST_OPS_ENGINE_CHECKOUT" "$binary" "$engine_revision" "$FKST_OPS_ENGINE_CONFIGURATION" \
    | python3 "$FKST_OPS_HOST_ROOT/../ops/invoke_provider.py" "$FKST_OPS_ENGINE_PROVIDER" fkst.ops.engine.v1) || return $?
  python3 "$FKST_OPS_HOST_ROOT/../ops/revision_derivation.py" assert \
    "$FKST_OPS_ENGINE_REVISION_CHECKOUT" "$FKST_OPS_ENGINE_REVISION_PATH" \
    "$platform_revision" "$engine_revision" || return $?
  source_revision=$(printf '%s\n' "$response" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["result"]["source_rev"])') || return $?
  [ "$source_revision" = "$engine_revision" ] || {
    echo "engine provider revision mismatch: expected $engine_revision, returned $source_revision" >&2
    return 1
  }
  printf '%s\n' "$response"
}

bootstrap_bin_on_total_miss() {
  if [ -n "${FKST_OPS_ENGINE_PROVIDER:-}" ]; then
    bootstrap_bin_on_total_miss_ops "$@"
    return $?
  fi
  local repo_root="$1" result_var="${2:-}" pin owner repo ref cache_root bin_path checkout_dir parent_dir lock_dir repo_url

  if [ -n "${FKST_NO_AUTOBUILD:-}" ]; then
    echo "error: fkst-framework binary not found and FKST_NO_AUTOBUILD is set; refusing network clone or build" >&2
    echo "  fix: set BIN to an executable fkst-framework, put fkst-framework on PATH, set FKST_SUBSTRATE_CHECKOUT to a built engine source, or unset FKST_NO_AUTOBUILD" >&2
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
  if [ -x "$bin_path" ]; then
    bootstrap_set_result "$result_var" hit
    printf '%s\n' "$bin_path"
    return 0
  fi

  checkout_dir="${bin_path%/target/debug/fkst-framework}"
  parent_dir="$(dirname "$checkout_dir")"
  mkdir -p "$parent_dir"
  lock_dir="$checkout_dir.lock"

  local lock_rc=0
  bootstrap_with_lock "$lock_dir" "$bin_path" || lock_rc=$?
  if [ "$lock_rc" -eq "$BOOTSTRAP_LOCK_ARTIFACT_READY" ]; then
    bootstrap_set_result "$result_var" hit
    printf '%s\n' "$bin_path"
    return 0
  fi
  if [ "$lock_rc" -ne 0 ]; then
    return "$lock_rc"
  fi
  if [ -x "$bin_path" ]; then
    rm -rf "$lock_dir"
    bootstrap_set_result "$result_var" hit
    printf '%s\n' "$bin_path"
    return 0
  fi

  echo "fkst-framework pinned source cache miss for $pin; bootstrapping pinned source (build starting)" >&2
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
    bootstrap_set_result "$result_var" build
  else
    local rc=$?
    rm -rf "$lock_dir"
    return "$rc"
  fi
}
