#!/usr/bin/env bash
# Resolve the engine binary; delegate any build to the declared engine provider.

FKST_OPS_HOST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_bin_validate_candidate() {
  local candidate="$1"
  if [ -f "$candidate" ] && [ -x "$candidate" ]; then
    return 0
  fi
  RESOLVE_BIN_ERROR="ENGINE_BINARY_UNAVAILABLE: declared build path: $candidate"
  return 1
}

resolve_bin_contract() {
  local repo_root="$1" mode="${2:-bootstrap}" candidate="" response
  RESOLVED_BIN=""
  RESOLVE_BIN_ERROR=""
  if [ -n "${BIN:-}" ]; then
    resolve_bin_validate_candidate "$BIN" || return $?
    RESOLVED_BIN="$BIN"; return 0
  fi
  if [ -f "$repo_root/.fkst/env" ]; then
    candidate="$(grep -E '^BIN=' "$repo_root/.fkst/env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    candidate="${candidate%%[[:space:]]#*}"
    candidate="${candidate%\"}"; candidate="${candidate#\"}"; candidate="${candidate%\'}"; candidate="${candidate#\'}"
    if [ -n "$candidate" ]; then
      resolve_bin_validate_candidate "$candidate" || return $?
      RESOLVED_BIN="$candidate"; return 0
    fi
  fi
  if command -v fkst-framework >/dev/null 2>&1; then
    candidate="$(command -v fkst-framework)"
    resolve_bin_validate_candidate "$candidate" || return $?
    RESOLVED_BIN="$candidate"; return 0
  fi
  if [ "$mode" = readonly ] || [ -n "${FKST_NO_AUTOBUILD:-}" ]; then
    RESOLVE_BIN_ERROR="set BIN to an executable engine binary or configure the declared engine provider"
    return 1
  fi
  response="$(bootstrap_bin_on_total_miss)" || return $?
  RESOLVED_BIN="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["binary"])')"
  resolve_bin_validate_candidate "$RESOLVED_BIN" || return $?
}

bootstrap_bin_on_total_miss() {
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
