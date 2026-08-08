#!/usr/bin/env bash
# Resolve the engine binary; delegate any build to the declared engine provider.

FKST_OPS_HOST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_bin_contract() {
  local repo_root="$1" mode="${2:-bootstrap}" candidate="" response
  RESOLVED_BIN=""
  RESOLVE_BIN_ERROR=""
  if [ -n "${BIN:-}" ]; then
    [ -x "$BIN" ] || { RESOLVE_BIN_ERROR="explicit BIN is not executable: $BIN"; return 1; }
    RESOLVED_BIN="$BIN"; return 0
  fi
  if [ -f "$repo_root/.fkst/env" ]; then
    candidate="$(grep -E '^BIN=' "$repo_root/.fkst/env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    candidate="${candidate%%[[:space:]]#*}"
    candidate="${candidate%\"}"; candidate="${candidate#\"}"; candidate="${candidate%\'}"; candidate="${candidate#\'}"
    if [ -n "$candidate" ]; then
      [ -x "$candidate" ] || { RESOLVE_BIN_ERROR=".fkst/env BIN is not executable: $candidate"; return 1; }
      RESOLVED_BIN="$candidate"; return 0
    fi
  fi
  if command -v fkst-framework >/dev/null 2>&1; then
    RESOLVED_BIN="$(command -v fkst-framework)"; return 0
  fi
  if [ "$mode" = readonly ] || [ -n "${FKST_NO_AUTOBUILD:-}" ]; then
    RESOLVE_BIN_ERROR="set BIN to an executable engine binary or configure the declared engine provider"
    return 1
  fi
  response="$(bootstrap_bin_on_total_miss)" || return $?
  RESOLVED_BIN="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["binary"])')"
  [ -x "$RESOLVED_BIN" ] || { RESOLVE_BIN_ERROR="engine provider returned a non-executable binary"; return 1; }
}

bootstrap_bin_on_total_miss() {
  # engine-provider-configuration: forward committed binding configuration as typed input.
  : "${FKST_OPS_ENGINE_PROVIDER:?declared engine provider is required}"
  : "${FKST_OPS_ENGINE_CHECKOUT:?resolved engine checkout is required}"
  : "${FKST_OPS_ENGINE_BINARY:?resolved engine binary is required}"
  : "${FKST_OPS_ENGINE_BRANCH:?resolved engine branch is required}"
  : "${FKST_OPS_ENGINE_CONFIGURATION:?resolved engine provider configuration is required}"
  python3 -c 'import json,sys; c=json.loads(sys.argv[4]); print(json.dumps({"engine_checkout":sys.argv[1],"engine_binary":sys.argv[2],"expected_branch":sys.argv[3],"operation":"build","build_command":c["build_command"]}))' \
    "$FKST_OPS_ENGINE_CHECKOUT" "$FKST_OPS_ENGINE_BINARY" "$FKST_OPS_ENGINE_BRANCH" "$FKST_OPS_ENGINE_CONFIGURATION" \
    | python3 "$FKST_OPS_HOST_ROOT/../ops/invoke_provider.py" "$FKST_OPS_ENGINE_PROVIDER" fkst.ops.engine.v1
}
