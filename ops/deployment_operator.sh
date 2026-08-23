#!/usr/bin/env bash
# deployment_operator.sh - execute the operator surface for declared deployments.
# It resolves declared topology and delegates one-host launch invariants to this
# repository's host-run supervise contract.
set -uo pipefail

# ---- validated deployment input ----
PYTHON="${FKST_OPS_PYTHON:-python3}"
# Python bootstraps profile parsing, so the interpreter that actually started is the authority for
# the child contract; it cannot be selected from the profile the operator has not parsed yet.
resolve_deployment_python() { "$PYTHON" -c 'import sys; print(sys.executable)'; }
DEPLOYMENT_PYTHON="$(resolve_deployment_python)" || exit $?
_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_repo_root="$(git -C "$_self_dir" rev-parse --show-toplevel 2>/dev/null || true)"
source "$_self_dir/deployment_launch_environment.sh"
source "$_self_dir/deployment_source_control.sh"
: "${FKST_OPS_DECLARATION:?FKST_OPS_DECLARATION is required}"
: "${FKST_OPS_MACHINE_PROFILE:?FKST_OPS_MACHINE_PROFILE is required}"
: "${FKST_OPS_LOCK:?FKST_OPS_LOCK is required}"
RESOLVED_DECLARATION="$(PYTHONPATH="$_repo_root${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -P -m schema.validator \
  "$FKST_OPS_DECLARATION" "$FKST_OPS_MACHINE_PROFILE" "$FKST_OPS_LOCK")" || exit $?
MECHANISM_TOOL_ASSIGNMENTS="$(PYTHONPATH="$_repo_root${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -P -c '
import os, shlex, sys, tomllib
from pathlib import Path
from schema.mechanism_tools import MECHANISM_TOOLS
with open(sys.argv[1], "rb") as stream:
    profile_tools = tomllib.load(stream).get("tools", {})
child_path = [sys.argv[2], str(Path(sys.argv[3]).parent)]
child_path.extend(str(Path(value).parent) for value in profile_tools.values() if value)
for name, tool in MECHANISM_TOOLS.items():
    value = (
        os.environ.get(tool.environment, "") if tool.environment is not None else ""
    ) or profile_tools.get(name, "")
    if tool.required and not value:
        raise SystemExit(f"error: machine profile has no carried mechanism tool: {name}")
    if tool.shell_variable is not None:
        print(f"{tool.shell_variable}={shlex.quote(value)}")
standard_path = os.confstr("CS_PATH") or os.defpath
child_path.extend(os.get_exec_path({"PATH": standard_path}))
print(f"DEPLOYMENT_CHILD_PATH={shlex.quote(os.pathsep.join(dict.fromkeys(child_path)))}")
' "$FKST_OPS_MACHINE_PROFILE" "$_self_dir" "$DEPLOYMENT_PYTHON")" || exit $?
eval "$MECHANISM_TOOL_ASSIGNMENTS"
DEPLOYMENT_OPERATOR_DEPLOYMENTS="$(printf '%s' "$RESOLVED_DECLARATION" | "$PYTHON" -c \
  'import json,sys; print(" ".join(item["id"] for item in json.load(sys.stdin)["deployment"]))')"

# The shared devloop family = the PLATFORM (like GitHub runners + marketplace actions), loaded from the
# platform checkout's repo-root packages/ (PKGSRC). Each target-source-
# primary TARGET repo (host) commits its OWN custom Lua packages under `.fkst/local-packages/<pkg>`
# (root stays website source) — so platform packages come from `$PKGSRC/packages/<pkg>`, a host's own package
# from `$HOST/.fkst/local-packages/<pkg>`. (`.fkst/` is a tracked+ignored runtime INTERFACE dir, not
# "all runtime": host repos may commit their own Lua there.)
# Platform packages every deployment supervise LOADS + RUNS from PKGSRC/packages/ are selected by the
# target host's `fkst.workspace.toml`. Non-self hosts use
# the declared platform external source's packages; the self host uses explicit workspace
# `[[package]]` entries. `deployment_operator.sh` only derives the launch argument from that manifest and never
# rewrites it, so a drift between committed composition and launch composition fails closed in the
# host-run contract instead of being masked.
DEVLOOP_PKGS=""

# cfg <id> consumes only the schema validator's resolved output.
cfg() {
  local values
  values="$(printf '%s' "$RESOLVED_DECLARATION" | "$PYTHON" -c '
import json,sys
name=sys.argv[1]
items=json.load(sys.stdin)["deployment"]
dep=next((item for item in items if item["id"] == name), None)
if dep is None:
    raise SystemExit(1)
m=dep["machine"]
profile=dep.get("github_devloop_profile", {})
empty="__FKST_OPS_EMPTY__"
def provider(field):
    binding=dep["providers"][field]
    return [binding["executable"],binding["contract"],json.dumps(binding["configuration"],separators=(",",":"))]
def source_pin(role):
    value=dep["sources"].get(role, {}).get("resolved")
    return json.dumps(value,separators=(",",":")) if value is not None else empty
claim=dep["claim_posture"]
authorization=dep["author_authorization"]
derivation=dep["engine_revision"]
package_sources=[]
for source in dep.get("package_sources",[]):
    value={"root":source["checkout"],"git":source["git"],"packages":source["packages"]}
    if "resolved" in source:
        value["resolved"]=source["resolved"]
    package_sources.append(value)
fields=[dep["target_identity"],m["target_checkout"],m["platform_checkout"],m["engine_checkout"],m["engine_binary"],m["durable"],m["runtime"],m["logs"],m.get("rate_pool", empty),m.get("bot_login", empty),json.dumps(dep["managed_bot_logins"],separators=(",",":")),json.dumps(authorization["authorized_logins"],separators=(",",":")),"1" if authorization["authorize_org_members"] else "0","1" if authorization["authorize_repo_collaborators"] else "0",dep["integration"]["upstream_branch"],dep["integration"]["integration_branch"],dep["integration"]["rollup_merge"],claim["mode"],"1" if claim["label_exclusive"] else "0"," ".join(dep["packages"]["host"]) or empty," ".join(dep["packages"].get("platform",[])) or empty,json.dumps(package_sources,separators=(",",":")),json.dumps(profile,separators=(",",":")),dep["sources"]["target"]["git"],dep["sources"]["platform"]["git"],dep["sources"]["engine"]["git"],source_pin("target"),source_pin("platform"),source_pin("engine"),m["platform_checkout"],derivation["path"],*provider("github_credential"),*provider("engine"),*provider("board_engine_durable"),*provider("board_github_control"),dep["integration"].get("local_test_command","")]
print("\t".join(fields))
' "$1")" || { echo "unknown deployment: $1" >&2; return 1; }
  IFS=$'\t' read -r REPO HOST PKGSRC ENGINE_CHECKOUT BIN DUR RUNTIME_ROOT LOGDIR RATE_POOL BOT MANAGED_BOT_LOGINS AUTHORIZED_LOGINS AUTHORIZE_ORG_MEMBERS AUTHORIZE_REPO_COLLABORATORS UPSTREAM_BRANCH INTEGRATION_BRANCH ROLLUP_MERGE CLAIM_MODE CLAIM_LABEL_EXCLUSIVE LOCAL_PKGS DECLARED_PLATFORM_PKGS DECLARED_PACKAGE_SOURCES GITHUB_DEVLOOP_PROFILE TARGET_GIT_URL PLATFORM_GIT_URL ENGINE_GIT_URL TARGET_SOURCE_PIN PLATFORM_SOURCE_PIN ENGINE_SOURCE_PIN REVISION_SOURCE ENGINE_REVISION_PATH GITHUB_CREDENTIAL_PROVIDER GITHUB_CREDENTIAL_CONTRACT GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION ENGINE_PROVIDER ENGINE_CONTRACT ENGINE_PROVIDER_CONFIGURATION ENGINE_BOARD_PROVIDER ENGINE_BOARD_CONTRACT ENGINE_BOARD_PROVIDER_CONFIGURATION GITHUB_BOARD_PROVIDER GITHUB_BOARD_CONTRACT GITHUB_BOARD_PROVIDER_CONFIGURATION LOCAL_TEST_COMMAND <<<"$values"
  [ "$RATE_POOL" = "__FKST_OPS_EMPTY__" ] && RATE_POOL=""
  [ "$BOT" = "__FKST_OPS_EMPTY__" ] && BOT=""
  [ "$LOCAL_PKGS" = "__FKST_OPS_EMPTY__" ] && LOCAL_PKGS=""
  [ "$DECLARED_PLATFORM_PKGS" = "__FKST_OPS_EMPTY__" ] && DECLARED_PLATFORM_PKGS=""
  [ "$TARGET_SOURCE_PIN" = "__FKST_OPS_EMPTY__" ] && TARGET_SOURCE_PIN=""
  [ "$PLATFORM_SOURCE_PIN" = "__FKST_OPS_EMPTY__" ] && PLATFORM_SOURCE_PIN=""
  [ "$ENGINE_SOURCE_PIN" = "__FKST_OPS_EMPTY__" ] && ENGINE_SOURCE_PIN=""
  ENGINE_BINARY_BASE="$BIN"
  CARGO="$("$PYTHON" -c 'import json, pathlib, sys; command=json.loads(sys.argv[1])["build_command"]; print(command[0] if pathlib.Path(command[0]).name == "cargo" else "")' "$ENGINE_PROVIDER_CONFIGURATION")" || return 1
}

# The declaration owns the platform package list. A target repository may still carry its own
# `fkst.workspace.toml`, and it is consulted only when the declaration is silent, so a target
# that says nothing about fkst needs no file at all. Reading the composition from the repository
# being operated made that repository carry configuration for its own operator; the declaration
# already held the same list, so this removes a second copy rather than adding a first.
derive_devloop_pkgs_from_workspace() { # $1 name
  local name="$1" output
  if [ -n "${DECLARED_PLATFORM_PKGS:-}" ]; then
    DEVLOOP_PKGS="$DECLARED_PLATFORM_PKGS"
    return 0
  fi
  output="$("$PYTHON" "$_self_dir/workspace_manifest.py" platform-packages "$name" "$HOST" "$PKGSRC" "$PLATFORM_GIT_URL")" \
    || { printf '%s\n' "$output" >&2; return 1; }
  DEVLOOP_PKGS="$output"
}

git_lock_sweep() { # $1 deployment name; remaining arguments are repository/worktree roots
  local name="$1" root_args=() lsof_args=() root
  shift
  [ -n "${LSOF:-}" ] && lsof_args+=(--lsof "$LSOF")
  for root in "$@"; do
    root_args+=(--root "$root")
  done
  "$PYTHON" "$_self_dir/git_lock_sweep.py" "${lsof_args[@]}" "${root_args[@]}" \
    | sed "s/^/[$name] /"
}

pidof_df() { pgrep -f -- "supervise --project-root ${HOST} " 2>/dev/null; }
latest_log() { ls -t "$LOGDIR/${1}-sv-"*.log 2>/dev/null | head -1; }
engine_panic_count() { # $1 supervise log; count engine panics, excluding child stderr= blobs
  sed -E 's/[[:space:]]stderr=.*$//' "$1" 2>/dev/null \
    | grep -aciE "thread '[^']*' panicked|panicked at|redb.*lock error" || true
}
pid_alive_non_zombie() {
  local pid="$1" stat
  kill -0 "$pid" 2>/dev/null || return 1
  stat=$(ps -o stat= -p "$pid" 2>/dev/null | awk 'NF {print $1; exit}')
  [[ "$stat" == Z* ]] && return 1
  return 0
}
supervise_ready_log() {
  local log="$1"
  # Supervise-owned startup readiness contract, not a generic health semantic.
  grep -qaE 'EVENT=code_provenance .*ENGINE_VER=[^ ]+ .*PKG_VERS=[^ ]+' "$log" 2>/dev/null \
    && grep -qa 'MSG=event runtime running' "$log" 2>/dev/null
}
wait_supervise_ready() { # $1 pid, $2 log
  # timeout_attempts is a GENEROUS backstop for a slow-but-healthy cold start, NOT a health SLA:
  # a supervise loading ~16 package roots + engine init emits its readiness markers
  # (code_provenance + `event runtime running`) ~14-16s after spawn (measured), so the old 10s
  # (100 * 0.1s) cap was chronically SHORTER than a normal cold start and cried wolf
  # ("FAILED to become ready" on a supervise that was in fact starting fine). 60s (600 * 0.1s)
  # clears the measured cold start with 3-4x headroom for backlog/disk pressure. This does NOT
  # slow real-failure detection: a dead start returns 1 the instant the pid dies (below), regardless
  # of the cap; the cap only bounds how long we wait for an ALIVE-but-not-yet-ready process.
  local pid="$1" log="$2" attempts=0 ready_seen=0 stable_attempts=30 timeout_attempts=600
  while [ "$attempts" -lt "$timeout_attempts" ]; do
    if ! pid_alive_non_zombie "$pid"; then
      return 1
    fi
    if supervise_ready_log "$log"; then
      ready_seen=1
    fi
    if [ "$ready_seen" -eq 1 ] && [ "$attempts" -ge "$stable_attempts" ]; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 0.1
  done
  return 2
}
expand() { [ "${1:-all}" = all ] && echo "$DEPLOYMENT_OPERATOR_DEPLOYMENTS" || echo "$1"; }

invoke_provider() { PATH="$DEPLOYMENT_CHILD_PATH" "$PYTHON" "$_self_dir/invoke_provider.py" "$1" "$2"; }

resolve_github_writer() {
  local auth_report resolved
  REAL_GH="${FKST_GITHUB_REAL_GH:-${REAL_GH:-}}"
  [ -n "$REAL_GH" ] || {
    echo "error: real gh executable not carried in machine profile" >&2
    return 1
  }
  auth_report="$("$REAL_GH" auth status --active --hostname github.com 2>&1)" || {
    echo "error: cannot determine the active GitHub CLI account" >&2
    return 1
  }
  resolved="$(printf '%s\n' "$auth_report" | "$PYTHON" -c '
import re, sys
lines = sys.stdin.read().splitlines()
accounts = []
for line in lines:
    match = re.match(r"^\s*[✓X] Logged in to github[.]com account (.+) \(([^()]*)\)\s*$", line)
    if match:
        accounts.append(match.groups())
active = [line for line in lines if re.match(r"^\s*- Active account: true\s*$", line)]
if len(accounts) != 1 or len(active) != 1:
    raise SystemExit(1)
login, source = accounts[0]
if not login or not source or "\t" in login or "\t" in source:
    raise SystemExit(1)
print(login + "\t" + source)
')" || {
    echo "error: active GitHub CLI account report is ambiguous or not parseable" >&2
    return 1
  }
  IFS="$(printf '\t')" read -r GITHUB_WRITER_LOGIN GITHUB_WRITER_SOURCE <<EOF
$resolved
EOF
  [ -n "$GITHUB_WRITER_LOGIN" ] && [ -n "$GITHUB_WRITER_SOURCE" ] || {
    echo "error: active GitHub CLI account report did not resolve an identity and credential source" >&2
    return 1
  }
}

authorize_github_writer() {
  [ -n "${GITHUB_CREDENTIAL_PROVIDER:-}" ] && [ -x "$GITHUB_CREDENTIAL_PROVIDER" ] || {
    echo "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY MSG=credential-helper-unavailable" >&2
    return 1
  }
  credential_source=$(printf '%s' "$GITHUB_CREDENTIAL_PROVIDER_CONFIGURATION" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["source"])') || return 1
  case "$credential_source" in
    github-app|github-cli-user) ;;
    *)
      echo "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY MSG=credential-source-unsupported" >&2
      return 1
      ;;
  esac
  REAL_GH="${FKST_GITHUB_REAL_GH:-${REAL_GH:-}}"
  GITHUB_CREDENTIAL_RESOLVER="${FKST_GITHUB_CREDENTIAL_RESOLVER:-${GITHUB_CREDENTIAL_RESOLVER:-}}"
  [ -n "$REAL_GH" ] || {
    echo "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY MSG=real-gh-unavailable" >&2
    return 1
  }
  [ "$credential_source" != github-app ] || [ -n "$GITHUB_CREDENTIAL_RESOLVER" ] || {
    echo "LEVEL=ERROR tag=FAILURE error_class=github-authentication-failed HEALTH=UNHEALTHY MSG=github-app-resolver-unavailable" >&2
    return 1
  }
  env -u GH_TOKEN -u GITHUB_TOKEN FKST_GITHUB_CREDENTIAL_HELPER="$GITHUB_CREDENTIAL_PROVIDER" \
    FKST_GITHUB_CREDENTIAL_SOURCE="$credential_source" FKST_GITHUB_CREDENTIAL_RESOLVER="$GITHUB_CREDENTIAL_RESOLVER" \
    FKST_GITHUB_REAL_GH="$REAL_GH" FKST_GITHUB_REPO="$REPO" FKST_GITHUB_BOT_LOGIN="$BOT" \
    "$PYTHON" "$_self_dir/github_credential_gh.py" --fkst-auth-check || return 1
  GITHUB_WRITER_LOGIN="$BOT"
}

resolve_engine_pair() {
  local values
  values=$("$PYTHON" "$_repo_root/ops/revision_derivation.py" resolve \
    "$REVISION_SOURCE" "$ENGINE_REVISION_PATH") || return $?
  IFS=$'\t' read -r PLATFORM_REVISION ENGINE_REVISION <<<"$values"
  [ -n "$PLATFORM_REVISION" ] && [ -n "$ENGINE_REVISION" ] || {
    echo "engine revision resolution failed: empty platform/engine pair" >&2
    return 1
  }
  BIN="${ENGINE_BINARY_BASE}-${ENGINE_REVISION}"
}

assert_engine_pair() { # $1 captured platform revision, $2 captured engine revision
  "$PYTHON" "$_repo_root/ops/revision_derivation.py" assert \
    "$REVISION_SOURCE" "$ENGINE_REVISION_PATH" "$1" "$2"
}

assert_engine_pair_at() { # $1 platform checkout, $2 platform revision, $3 engine revision
  "$PYTHON" "$_repo_root/ops/revision_derivation.py" assert \
    "$1" "$ENGINE_REVISION_PATH" "$2" "$3"
}

launch_platform_snapshot_valid() { # $1 snapshot, $2 P, $3 E
  local snapshot_revision
  snapshot_revision=$(git -C "$1" rev-parse --verify HEAD^{commit} 2>/dev/null) || return 1
  [ "$snapshot_revision" = "$2" ] || return 1
  assert_engine_pair_at "$1" "$2" "$3" >/dev/null 2>&1
}

materialise_launch_platform() { # $1 source checkout, $2 destination, $3 P, $4 E
  local source="$1" destination="$2" platform_revision="$3" engine_revision="$4"
  local source_identity temporary lock guard status
  mkdir -p "$(dirname "$destination")" || return 1
  lock="$RUNTIME_ROOT/.platform-locks/$platform_revision.lock"
  guard="$RUNTIME_ROOT/.platform-locks/.identity.guard"
  if [ -e "$destination" ] && ! launch_platform_snapshot_valid "$2" "$3" "$4"; then
    "$PYTHON" "$_self_dir/launch_child.py" --remove-unlocked-snapshot \
      "$destination" "$lock" "$guard"
    status=$?
    if [ "$status" -ne 0 ]; then
      [ "$status" -eq 75 ] || echo "LAUNCH_PLATFORM_REBUILD_FAILED: cannot remove invalid snapshot" >&2
      return 1
    fi
  fi
  if [ ! -e "$destination" ]; then
    temporary=$(mktemp -d "$(dirname "$destination")/.${platform_revision}.XXXXXX") || return 1
    git clone --quiet --no-checkout "$source" "$temporary" 2>/dev/null || {
      rm -rf "$temporary"
      echo "LAUNCH_PLATFORM_SNAPSHOT_FAILED: cannot clone captured platform revision" >&2
      return 1
    }
    git -C "$temporary" checkout --quiet --detach "$platform_revision" 2>/dev/null || {
      rm -rf "$temporary"
      echo "LAUNCH_PLATFORM_SNAPSHOT_FAILED: cannot check out captured platform revision" >&2
      return 1
    }
    source_identity=$(git -C "$source" config --get remote.origin.url 2>/dev/null || true)
    [ -n "$source_identity" ] || source_identity="$source"
    git -C "$temporary" remote set-url origin "$source_identity" 2>/dev/null || {
      rm -rf "$temporary"
      echo "LAUNCH_PLATFORM_SNAPSHOT_FAILED: cannot bind captured platform source identity" >&2
      return 1
    }
    launch_platform_snapshot_valid "$temporary" "$platform_revision" "$engine_revision" || {
      rm -rf "$temporary"
      echo "LAUNCH_PLATFORM_SNAPSHOT_MISMATCH: materialised snapshot HEAD does not match captured platform revision or platform/engine pair assertion failed" >&2
      return 1
    }
    "$PYTHON" -c 'import os,sys; os.rename(sys.argv[1],sys.argv[2])' \
      "$temporary" "$destination" 2>/dev/null || rm -rf "$temporary"
  fi
  launch_platform_snapshot_valid "$2" "$3" "$4" || {
    echo "LAUNCH_PLATFORM_SNAPSHOT_MISMATCH: captured snapshot HEAD does not match captured platform revision or platform/engine pair assertion failed" >&2
    return 1
  }
}

engine_build_receipt_current() {
  "$PYTHON" "$_repo_root/ops/revision_derivation.py" receipt-current \
    "$BIN" "$ENGINE_REVISION" "$ENGINE_PROVIDER_CONFIGURATION"
}

invoke_engine_build_provider() {
  # engine-provider-configuration: forward committed binding configuration as typed input.
  "$PYTHON" -c 'import json,sys; c=json.loads(sys.argv[4]); print(json.dumps({"engine_checkout":sys.argv[1],"engine_binary":sys.argv[2],"expected_revision":sys.argv[3],"operation":"build","build_command":c["build_command"]}))' "$ENGINE_CHECKOUT" "$BIN" "$ENGINE_REVISION" "$ENGINE_PROVIDER_CONFIGURATION" \
    | invoke_provider "$ENGINE_PROVIDER" "$ENGINE_CONTRACT"
}

ensure_engine_binary_current() {
  local response source_revision pinned_revision
  resolve_engine_pair || return $?
  if [ -n "${ENGINE_SOURCE_PIN:-}" ]; then
    pinned_revision=$(source_pin_values "$ENGINE_SOURCE_PIN") || {
      echo "ENGINE-SOURCE-PIN-INVALID: cannot parse engine source pin" >&2
      return 1
    }
    [ "$pinned_revision" = "$ENGINE_REVISION" ] || {
      echo "ENGINE-SOURCE-PIN-MISMATCH: expected derived engine revision $ENGINE_REVISION, got $pinned_revision" >&2
      return 1
    }
    sync_to_pinned_revision "$ENGINE_CHECKOUT" "$pinned_revision" || return 1
  fi
  if engine_build_receipt_current; then
    ENGINE_BUILD_STATUS="current: $BIN@${ENGINE_REVISION:0:8}"
    return 0
  fi
  response=$(invoke_engine_build_provider) || return $?
  source_revision=$(printf '%s\n' "$response" | "$PYTHON" -c \
    'import json,sys; print(json.load(sys.stdin)["result"]["source_rev"])') || return 1
  [ "$source_revision" = "$ENGINE_REVISION" ] || {
    echo "engine provider revision mismatch: expected $ENGINE_REVISION, returned $source_revision" >&2
    return 1
  }
  assert_engine_pair "$PLATFORM_REVISION" "$ENGINE_REVISION" || return 1
  engine_build_receipt_current || {
    echo "engine build receipt mismatch after provider success: $BIN@$ENGINE_REVISION" >&2
    return 1
  }
  ENGINE_BUILD_STATUS="built: $BIN@${ENGINE_REVISION:0:8}"
}

bin_ensure_fresh() {
  ensure_engine_binary_current || return $?
  printf '%s\n' "$ENGINE_BUILD_STATUS"
}

cmd_board() {
  local target="${1:-all}" n failed=0 tmp github_input engine_input
  for n in $(expand "$target"); do
    cfg "$n" || { failed=1; continue; }
    resolve_engine_pair || { failed=1; continue; }
    engine_build_receipt_current || {
      echo "ENGINE_BUILD_RECEIPT_MISMATCH: $BIN@$ENGINE_REVISION" >&2
      failed=1
      continue
    }
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/fkst-ops-board.XXXXXX") || return 1
    github_input="$tmp/github.json"; engine_input="$tmp/engine.json"
    "$PYTHON" -c 'import json,sys; p=json.loads(sys.argv[3]); p["stale_hours"]=int(sys.argv[6] or 6); json.dump({"target_identity":sys.argv[1],"platform_checkout":sys.argv[2],"profile":p,"bot_login":sys.argv[4],"managed_bot_set":json.loads(sys.argv[5])},open(sys.argv[7],"w"))' "$REPO" "$PKGSRC" "$GITHUB_DEVLOOP_PROFILE" "$BOT" "$MANAGED_BOT_LOGINS" "${2:-}" "$github_input"
    "$PYTHON" -c 'import json,sys; json.dump({"engine_binary":sys.argv[1],"durable_root":sys.argv[2],"cache":sys.argv[3],"refresh":False,"ttl_seconds":300,"stall_seconds":900},open(sys.argv[4],"w"))' "$BIN" "$DUR" "$tmp/cache.json" "$engine_input"
    FKST_EXPECTED_ENGINE_REVISION="$ENGINE_REVISION" "$PYTHON" "$_repo_root/board/board.py" --github-provider "$GITHUB_BOARD_PROVIDER" --engine-provider "$ENGINE_BOARD_PROVIDER" --github-input "$github_input" --engine-input "$engine_input" || failed=1
    rm -rf "$tmp"
  done
  return "$failed"
}

# Prune worktrees + scratch dirs from OLD runtime roots of this deployment (implement/fix
# depts create worktrees under the launch runtime scratch, registered in the shared .git; each
# restart makes a fresh runtime root, orphaning the old registrations — registry leak #500).
#
# PRESERVE STILL-REGISTERED GENERATIONS (#2925). A restart SIGKILLs only the supervise; an
# in-flight codex is ORPHANED and keeps running against its worktree (crash-only contract). This
# cleaner used to remove the registration and rm -rf the directory anyway, so the orphan kept
# writing into a deleted path and recreated a partial, UNREGISTERED husk. Harvest then ran `cd`
# into it, exited nonzero WITHOUT a typed marker, and the run was recorded as a false
# `impl-failed / local-iteration-attribution-indeterminate` (observed on #2919, and on #2925's own
# implementation twice). A registered worktree is the ground truth for "someone still owns this",
# so a generation that still has one is skipped entirely and reported — it is reclaimed on a later
# pass once its registration is gone. This is the operator-side containment that the #2925 fix
# (moving implementation worktrees to a stable root) requires to land first; without it, deploying
# that fix would itself destroy the pre-fix work still in flight.
clean_stale_runtime_worktrees() { # $1 name, $2 current-rt-to-keep
  local name="$1" keep="$2" d held writer_census writer_census_status
  held=$(git -C "$PKGSRC" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2}' \
    | grep -F "/${name}." | grep -vF "$keep")
  if [ -n "$held" ]; then
    echo "  ! preserving $(printf '%s\n' "$held" | wc -l | tr -d ' ') still-registered worktree(s) from older runtime roots (#2925):"
    printf '%s\n' "$held" | sed 's|^|      |'
  fi
  if [ -z "$LSOF" ]; then
    echo "[$name] cannot prove stale runtime writer quiescence: lsof unavailable; retaining old runtimes" >&2
    return 0
  fi
  git -C "$PKGSRC" worktree prune 2>/dev/null
  for d in "$RUNTIME_ROOT"/"${name}".*; do
    [ -d "$d" ] && [ "$d" != "$keep" ] || continue
    # Skip any generation that still holds a registered worktree; removing it is what
    # manufactures the husk. Re-read the registry each iteration: `worktree prune` above may
    # have dropped registrations whose directories are already gone.
    if git -C "$PKGSRC" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2}' \
        | grep -qF "$d/"; then
      continue
    fi

    # The killed supervisor cannot spawn new writers. Existing orphaned children only shrink this
    # holder set, so an empty kernel open-file census is the deletion barrier for the old runtime.
    writer_census_status=0
    writer_census=$("$LSOF" +D "$d" 2>&1) || writer_census_status=$?
    if [ "$writer_census_status" -eq 0 ] && [ -n "$writer_census" ]; then
      echo "[$name] retaining stale runtime with active writers: $d" >&2
      continue
    fi
    # lsof reports no matches as exit 1 with no output; every other result is inconclusive.
    if [ "$writer_census_status" -ne 1 ] || [ -n "$writer_census" ]; then
      echo "[$name] cannot prove stale runtime writer quiescence: lsof exit $writer_census_status; retaining $d" >&2
      [ -n "$writer_census" ] && printf '%s\n' "$writer_census" >&2
      continue
    fi
    "$PYTHON" "$_self_dir/dead_letter_causes.py" archive \
      --runtime-root "$d" --output "$LOGDIR/${name}-dead-letter-facts.log" \
      || { echo "[$name] could not retain dead-letter cause facts from $d" >&2; return 1; }
    rm -rf "$d" 2>/dev/null
  done
}

clean_stale_launch_platforms() { # $1 current platform snapshot
  local keep="$1" d lock guard status
  guard="$RUNTIME_ROOT/.platform-locks/.identity.guard"
  for d in "$RUNTIME_ROOT"/.platform/*; do
    [ -d "$d" ] && [ "$d" != "$keep" ] || continue
    lock="$RUNTIME_ROOT/.platform-locks/$(basename "$d").lock"
    "$PYTHON" "$_self_dir/launch_child.py" --remove-unlocked-snapshot "$d" "$lock" "$guard" || {
      status=$?
      [ "$status" -eq 75 ] && continue
      echo "LAUNCH_PLATFORM_RECLAIM_FAILED: cannot remove $d" >&2
      return 1
    }
  done
  for lock in "$RUNTIME_ROOT"/.platform-locks/*.lock; do
    [ -f "$lock" ] || continue
    d="$RUNTIME_ROOT/.platform/$(basename "$lock" .lock)"
    [ "$d" = "$keep" ] || [ -d "$d" ] && continue
    "$PYTHON" "$_self_dir/launch_child.py" --remove-unlocked-snapshot "$d" "$lock" "$guard" || {
      status=$?
      [ "$status" -eq 75 ] && continue
      echo "LAUNCH_PLATFORM_LOCK_RECLAIM_FAILED: cannot remove $lock" >&2
      return 1
    }
  done
}

clean_stale_engine_artifacts() {
  local n base existing status seen
  local bases=() selected=()
  for n in $(expand all); do
    cfg "$n" || return 1
    resolve_engine_pair || return 1
    selected+=("$BIN")
    seen=0
    for existing in ${bases[@]+"${bases[@]}"}; do
      [ "$existing" = "$ENGINE_BINARY_BASE" ] && seen=1
    done
    [ "$seen" -eq 1 ] || bases+=("$ENGINE_BINARY_BASE")
  done
  for base in ${bases[@]+"${bases[@]}"}; do
    "$PYTHON" "$_repo_root/ops/revision_derivation.py" reclaim-engine-artifacts \
      "$base" "${selected[@]}" || {
      status=$?
      [ "$status" -eq 75 ] && continue
      echo "ENGINE_ARTIFACT_RECLAIM_FAILED: cannot reclaim revisions under $base" >&2
      return 1
    }
  done
}

launch_one() { # $1 name, $2 restart flag (0|1)
  local name="$1" restart="${2:-0}" ts log rt launch_platform launch_lock platform_guard
  local engine_lock engine_guard pid
  local environment_sha256 declared_package_sources declared_package_source args=()
  clean_stale_engine_artifacts || return 1
  cfg "$name" || return 1
  ensure_engine_binary_current || return 1
  ts=$(date +%s); log="$LOGDIR/${name}-sv-${ts}.log"; rt="$RUNTIME_ROOT/${name}.${ts}"
  launch_platform="$RUNTIME_ROOT/.platform/$PLATFORM_REVISION"
  launch_lock="$RUNTIME_ROOT/.platform-locks/$PLATFORM_REVISION.lock"
  platform_guard="$RUNTIME_ROOT/.platform-locks/.identity.guard"
  engine_lock="$(dirname "$BIN")/.$(basename "$BIN").launch.lock"
  engine_guard="$(dirname "$ENGINE_BINARY_BASE")/.$(basename "$ENGINE_BINARY_BASE").locks.guard"
  derive_devloop_pkgs_from_workspace "$name" || return 1
  [ -n "$DEVLOOP_PKGS" ] || { echo "[$name] no platform packages: declaration names none and the target manifest fallback yielded none"; return 1; }
  authorize_github_writer || return 1
  resolve_deployment_child_environment || return 1
  environment_sha256=$(deployment_child_environment_sha256) || return 1

  clean_stale_launch_platforms "$launch_platform" || return 1
  materialise_launch_platform \
    "$PKGSRC" "$launch_platform" "$PLATFORM_REVISION" "$ENGINE_REVISION" || return 1
  engine_build_receipt_current || {
    echo "engine build receipt changed before launch: $BIN@$ENGINE_REVISION" >&2
    return 1
  }
  args=(
    "$_repo_root/host/supervise.sh"
    --project-root "$HOST"
    --platform-root "$launch_platform"
    --platform-packages "$DEVLOOP_PKGS"
    --expected-engine-revision "$ENGINE_REVISION"
    --durable-root "$DUR"
    --runtime-root "$rt"
  )
  [ -n "$LOCAL_PKGS" ] && args+=(--host-packages "$LOCAL_PKGS")
  # Package sources the declaration named beyond the platform. The validator has already bound
  # each name to a checkout, so the launch contract carries the binding rather than re-deriving it.
  declared_package_sources=$(package_source_launch_args) || {
    echo "[$name] declared package sources -> INVALID" >&2
    return 1
  }
  while IFS= read -r declared_package_source; do
    [ -n "$declared_package_source" ] && args+=("$declared_package_source")
  done <<<"$declared_package_sources"
  [ "$restart" = "1" ] && args+=(--restart)
  printf 'FKST_GITHUB_WRITE=%s FKST_GITHUB_WRITER_LOGIN=%s FKST_GITHUB_CLAIM_MODE=%s FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=%s LAUNCH_ENV_SHA256=%s\n' \
    "1" "$GITHUB_WRITER_LOGIN" "$CLAIM_MODE" \
    "$CLAIM_LABEL_EXCLUSIVE" "$environment_sha256" > "$log"
  pid=$(env -u GH_TOKEN -u GITHUB_TOKEN "${DEPLOYMENT_CHILD_ENVIRONMENT[@]}" \
    "$PYTHON" "$_self_dir/launch_child.py" --spawn "$log" "$launch_lock" "$platform_guard" \
      "$engine_lock" "$engine_guard" "${args[@]}" 2>> "$log" </dev/null) || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] || { echo "LAUNCH_CHILD_PID_INVALID: $pid" >&2; return 1; }
  ln -sf "$log" "$LOGDIR/${name}-sv.log"
  wait_supervise_ready "$pid" "$log"
  local ready_status=$?
  if [ "$ready_status" -eq 0 ]; then
    # Cleanup separately proves that no orphaned old-runtime writer remains.
    clean_stale_runtime_worktrees "$name" "$rt"
    # Committed per-launch verification that the own-session daemonization took effect: a session
    # leader has PGID == PID. If not, setsid silently did not apply and the supervise is back in a
    # foreign pgroup (the bug this launch fixes) — surface it loud rather than pass a false green.
    local svpgid; svpgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
    local own="own-pgroup=yes"; [ "$svpgid" = "$pid" ] || own="own-pgroup=NO(WARN: setsid not in effect, pgid=$svpgid — supervise is signal-group-vulnerable)"
    echo "[$name] started pid $pid  $own  panic=$(engine_panic_count "$log")  log=$log"
  else
    if [ "$ready_status" -eq 1 ]; then
      echo "[$name] FAILED to start; supervise pid $pid exited before readiness; tail:"
    else
      echo "[$name] FAILED to become ready; supervise pid $pid did not emit startup readiness; tail:"
    fi
    tail -12 "$log" | sed 's/\x1b\[[0-9;]*m//g'
    return 1
  fi
}

# launch_with_lock_retry: launch_one + a bounded retry on the redb lock race ONLY.
# `restart` is the deploy path and is NOT atomic: it SIGKILLs the old supervise then opens the
# durable store. That kill does not always release the redb lock in time; the race loser exits with
# `Database already open. Cannot acquire lock.` leaving NOTHING running — a full outage whose next
# signal is the following operator wake (incident 2026-08-01, #3001; a plain retry minutes later
# succeeded first try, so the lock was never genuinely held). Retry ONLY this signature, so a real
# failure (bad config, panic, missing BIN) still fails fast and loud on the first attempt.
# Deliberately NOT named launch_one: that name carries the host-run supervise delegation that
# G-DEPLOYMENT-OPERATOR-BOUNDARY audits, and this wrapper must not displace it from the audited surface.
launch_with_lock_retry() { # $1 name, $2 restart flag (0|1)
  local attempts=5 i=1 log
  while :; do
    launch_one "$1" "$2" && return 0
    log=$(ls -t "$LOGDIR/${1}-sv-"*.log 2>/dev/null | head -1)
    [ "$i" -lt "$attempts" ] && [ -n "$log" ] \
      && grep -q "Database already open. Cannot acquire lock." "$log" 2>/dev/null || return 1
    echo "[$1] durable lock not yet released by the previous supervise (attempt $i/$attempts); retrying in ${i}s"
    sleep "$i"; i=$((i + 1))
  done
}

start_one() {
  cfg "$1" || return 1
  local existing; existing=$(pidof_df)
  if [ -n "$existing" ]; then echo "[$1] already running (pid $existing) — use restart"; return 0; fi
  launch_with_lock_retry "$1" 0
}

stop_one() {
  cfg "$1" || return 1
  local p; p=$(pidof_df)
  if [ -z "$p" ]; then echo "[$1] not running"; return 0; fi
  if kill -9 "$p" 2>/dev/null; then
    echo "[$1] killed $p with SIGKILL"
    return 0
  fi
  echo "[$1] failed to SIGKILL $p" >&2
  return 1
}

restart_one() {
  cfg "$1" || return 1
  echo "[$1] sync to origin/$INTEGRATION_BRANCH (run branch; rollup target stays $UPSTREAM_BRANCH):"
  ensure_run_checkout "$PKGSRC" "$PLATFORM_GIT_URL" || return 1
  if [ "$HOST" != "$PKGSRC" ]; then
    ensure_run_checkout "$HOST" "$TARGET_GIT_URL" || return 1
  fi
  ensure_declared_package_source_checkouts || return 1
  derive_devloop_pkgs_from_workspace "$1" || return 1
  [ -n "$PLATFORM_SOURCE_PIN" ] || ensure_integration_caught_up "$PKGSRC"  # pinned sources must not advance
  if [ "$HOST" != "$PKGSRC" ] && [ -z "$TARGET_SOURCE_PIN" ]; then
    ensure_integration_caught_up "$HOST"
  fi
  sync_deployment_source "$PKGSRC" "$PLATFORM_SOURCE_PIN"
  if [ "$HOST" != "$PKGSRC" ]; then
    sync_deployment_source "$HOST" "$TARGET_SOURCE_PIN"
  fi
  sync_declared_package_sources || return 1
  # One migration bridge: a supervise launched before the host-run contract has no
  # durable pidfile yet, so --restart has nothing to kill on the first upgraded run.
  [ ! -f "$DUR/.fkst-supervise.pid" ] && { stop_one "$1"; sleep 1; }
  launch_with_lock_retry "$1" 1
}

# fmt_uptime <etime>: render `ps -o etime=` ([[DD-]HH:]MM:SS) with EXPLICIT units.
# The raw format's leading field changes meaning with the field count, so `09:30` (nine minutes) and
# `09:30:00` (nine hours) look alike at a glance — an operator read a 9m30s supervise uptime as 9h30m
# and started diagnosing a nine-hour stall on a twelve-minute-old process. The producer owns making
# this unambiguous; every reader of status/doctor/board gets it for free.
fmt_uptime() {
  local et="${1:-}" d=0 h=0 m=0 s=0 rest colons
  [ -n "$et" ] || { printf '?'; return 0; }
  rest="$et"
  case "$rest" in *-*) d=$((10#${rest%%-*})); rest=${rest#*-} ;; esac
  # `rest` is now [HH:]MM:SS — peel the hour field only when it is actually present, rather than
  # indexing a fixed offset (a negative subscript would be evaluated even on the branch that discards it)
  colons=${rest//[^:]/}
  if [ ${#colons} -ge 2 ]; then h=$((10#${rest%%:*})); rest=${rest#*:}; fi
  m=$((10#${rest%%:*})); s=$((10#${rest##*:}))
  if [ "$d" -gt 0 ]; then printf '%dd%02dh' "$d" "$h"
  elif [ "$h" -gt 0 ]; then printf '%dh%02dm' "$h" "$m"
  else printf '%dm%02ds' "$m" "$s"; fi
}

dead_letter_count() {
  local count
  count=$("$PYTHON" "$_self_dir/dead_letter_causes.py" status \
    --engine "$BIN" --durable-root "$DUR" 2>/dev/null) || count=unknown
  case "$count" in
    unknown) ;;
    ''|*[!0-9]*) count=unknown ;;
  esac
  printf '%s' "$count"
}

status_one() {
  cfg "$1" || return 1
  local p log; p=$(pidof_df); log=$(latest_log "$1")
  if [ -z "$p" ]; then echo "[$1] STOPPED   (target $REPO)"; return 0; fi
  local et panic auth_fail dead health last hv pv posture writer claim_mode claim_exclusive
  et=$(fmt_uptime "$(ps -o etime= -p $p 2>/dev/null | tr -d ' ')")
  panic=$(engine_panic_count "$log")
  auth_fail=$(grep -ac 'error_class=github-authentication-failed' "$log" 2>/dev/null || true)
  dead=$(dead_letter_count)
  health=HEALTHY
  if [ "$auth_fail" -ne 0 ] || [[ "$dead" =~ ^[1-9][0-9]*$ ]]; then
    health=UNHEALTHY
  elif [ "$dead" = unknown ]; then
    health=UNKNOWN
  fi
  last=$(tail -1 "$log" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-44)
  hv=$(git -C "$HOST" rev-parse HEAD 2>/dev/null | cut -c1-8)
  pv=$(git -C "$PKGSRC" rev-parse HEAD 2>/dev/null | cut -c1-8)
  posture=$(grep -aoE 'FKST_GITHUB_WRITE=(0|1)' "$log" 2>/dev/null | head -1 | cut -d= -f2)
  [ -n "$posture" ] || posture="unknown"
  writer=$(grep -aoE 'FKST_GITHUB_WRITER_LOGIN=[A-Za-z0-9-]+' "$log" 2>/dev/null | head -1 | cut -d= -f2)
  [ -n "$writer" ] || writer="unknown"
  claim_mode=$(grep -aoE 'FKST_GITHUB_CLAIM_MODE=(assignee|label)' "$log" 2>/dev/null | head -1 | cut -d= -f2)
  claim_exclusive=$(grep -aoE 'FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE=(0|1)' "$log" 2>/dev/null | head -1 | cut -d= -f2)
  [ -n "$claim_mode" ] || claim_mode="unknown"
  [ -n "$claim_exclusive" ] || claim_exclusive="unknown"
  printf '[%s] RUNNING pid %s up %s health=%s auth-fail=%s dead=%s panic=%s write=%s writer=%s claim=%s label-exclusive=%s | host@%s pkgs@%s | %s\n' "$1" "$p" "$et" "$health" "$auth_fail" "$dead" "$panic" "$posture" "$writer" "$claim_mode" "$claim_exclusive" "$hv" "$pv" "$last"
  # Preserve status_one's established exit contract: only authentication failures make it nonzero.
  [ "$auth_fail" -eq 0 ]
}

# _proc_stale <name> -> freshness verdict of the RUNNING process vs origin/dev. Authoritative =
# the code the process loaded at startup (logged code_provenance PKG_VERS/ENGINE_VER), NOT the
# worktree/BIN file (those can be updated without reloading the process — only a restart reloads).
# Echoes: stopped | current | skew (dev moved, declared non-executed files only) |
# pkg-stale (platform code may have changed) | engine-stale | environment-stale.
# PKG freshness is vs PKGSRC origin/$INTEGRATION_BRANCH (the run branch the deployment loads);
# ENGINE freshness is the revision derived from the captured platform commit.
platform_paths_require_restart() {
  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
      .github/*) ;; # CI-only configuration is not read by a running supervise.
      scripts/*_test.*) ;; # Test-only scripts run under verification, not supervise.
      scripts/check_repo*) ;; # Repository checker and lint scripts inspect source outside supervise.
      docs/*|.claude/skills/*|AGENTS.md|CLAUDE.md|CONTRIBUTING.md|README.md|SECURITY.md|LICENSE) ;;
      *) return 0 ;;
    esac
  done
  return 1
}

# Emits "<checkout root>\t<one package name>\t<exact revision or empty>" for each declared
# package source. One name is
# enough: the engine logs a commit per loaded package, and every package from one source carries
# that source's commit, so any one of them reports whether the source has moved.
_proc_stale() {
  cfg "$1" || { echo unknown; return; }
  local LC_ALL=C
  local p log platform_package package_versions procpkg proceng procenv desiredenv pdev changed_paths pin_revision skew=0; p=$(pidof_df); log=$(latest_log "$1")
  [ -z "$p" ] && { echo stopped; return; }
  derive_devloop_pkgs_from_workspace "$1" >/dev/null || { echo config-error; return; }
  if [ -n "${PLATFORM_SOURCE_PIN:-}" ]; then
    pin_revision=$(source_pin_values "$PLATFORM_SOURCE_PIN") || {
      echo pinned-source-invalid; return;
    }
    pdev="$pin_revision"
  else
    git -C "$PKGSRC" fetch origin "$INTEGRATION_BRANCH" -q 2>/dev/null
    pdev=$(git -C "$PKGSRC" rev-parse "origin/$INTEGRATION_BRANCH" 2>/dev/null)
  fi
  resolve_engine_pair || { echo engine-revision-failed; return; }
  platform_package="${DEVLOOP_PKGS%% *}"
  package_versions=$(provenance_package_versions "$log") || { echo pkg-stale; return; }
  procpkg=$(provenance_package_version "$package_versions" "$platform_package")
  proceng=$(grep -aoE 'ENGINE_VER=[a-f0-9]+' "$log" 2>/dev/null | tail -1 | cut -d= -f2)
  if [ -n "$proceng" ] && [ "${ENGINE_REVISION:0:${#proceng}}" != "$proceng" ]; then echo engine-stale; return; fi
  if [ -n "$procpkg" ] && [ "${pdev:0:${#procpkg}}" != "$procpkg" ]; then
    changed_paths=$(git -C "$PKGSRC" diff --name-only "$procpkg" "$pdev" -- 2>/dev/null) \
      || { echo pkg-stale; return; }
    if platform_paths_require_restart <<<"$changed_paths"; then echo pkg-stale; return; fi
    skew=1
  fi
  # A package source that has moved means the running process loaded code that no longer exists
  # upstream. Every path in a package source is package code, so unlike the platform there is no
  # documentation-only class to exempt: any advance is a reload.
  local package_source_verdict
  package_source_verdict=$(package_source_moved "$package_versions")
  [ -z "$package_source_verdict" ] || { echo "$package_source_verdict"; return; }
  procenv=$(grep -aoE 'LAUNCH_ENV_SHA256=[a-f0-9]{64}' "$log" 2>/dev/null | tail -1 | cut -d= -f2)
  [ -n "$procenv" ] || { echo environment-stale; return; }
  resolve_deployment_child_environment || { echo environment-contract-error; return; }
  desiredenv=$(deployment_child_environment_sha256) || { echo environment-contract-error; return; }
  [ "$procenv" = "$desiredenv" ] || { echo environment-stale; return; }
  [ "$skew" -eq 0 ] || { echo skew; return; }
  echo current
}

# cmd_sync: keep deployment-owned sources current in one call. The mechanism checkout is immutable:
# its version is the deployment lock pin. Advance target/platform run branches, update and rebuild
# each declared engine through its provider, then AUTO-RESTART only supervises whose RUNNING code may have changed
# (pkg-stale/engine-stale/environment-stale). Unverifiable package provenance, skill/docs-only
# skew, and already-current processes are left running - a restart cannot repair provenance and
# would only churn in-flight codex for no code change.
cmd_sync() {
  local n st failed=0 platform_pin target_pin package_source_checkouts source_root source_url
  local package_source_roots=()
  for n in $(expand "${1:-all}"); do
    cfg "$n" || { failed=1; continue; }
    package_source_checkouts=$(declared_package_source_checkouts) || {
      echo "[$n] declared package sources -> INVALID" >&2
      failed=1
      continue
    }
    package_source_roots=()
    while IFS=$'\t' read -r source_root source_url; do
      [ -n "$source_root" ] && package_source_roots+=("$source_root")
    done <<<"$package_source_checkouts"
    ensure_declared_package_source_checkouts || { failed=1; continue; }
    # Reap orphaned locks before synchronization writes: a lock left by a dead process blocks
    # every subsequent update, and the sweep keeps any lock it cannot prove unheld.
    if [ -n "$package_source_checkouts" ]; then
      git_lock_sweep "$n" "$HOST" "$PKGSRC" "$RUNTIME_ROOT" \
        "${package_source_roots[@]}" || { failed=1; continue; }
    else
      git_lock_sweep "$n" "$HOST" "$PKGSRC" "$RUNTIME_ROOT" \
        || { failed=1; continue; }
    fi
    platform_pin="${PLATFORM_SOURCE_PIN:-}"
    target_pin="${TARGET_SOURCE_PIN:-}"
    if [ -n "$platform_pin" ] || [ -n "$target_pin" ]; then
      echo "[$n] deployment source checkouts (pinned revisions where declared):"
    else
      echo "[$n] deployment source checkouts -> origin/$INTEGRATION_BRANCH:"
    fi
    derive_devloop_pkgs_from_workspace "$n" || { echo "  $n: config-error"; failed=1; continue; }
    [ -n "$platform_pin" ] || ensure_integration_caught_up "$PKGSRC"  # pinned sources must not advance
    if [ "$HOST" != "$PKGSRC" ] && [ -z "$target_pin" ]; then
      ensure_integration_caught_up "$HOST"
    fi
    if [ -n "$platform_pin" ]; then
      sync_deployment_source "$PKGSRC" "$platform_pin" || { failed=1; continue; }
    else
      sync_to_run_branch "$PKGSRC" || { failed=1; continue; }
    fi
    if [ "$HOST" != "$PKGSRC" ]; then
      if [ -n "$target_pin" ]; then
        sync_deployment_source "$HOST" "$target_pin" || { failed=1; continue; }
      else
        sync_to_run_branch "$HOST" || { failed=1; continue; }
      fi
    fi
    sync_declared_package_sources || { failed=1; continue; }
    echo "[$n] engine BIN:"
    bin_ensure_fresh | sed 's/^/  /' || { failed=1; continue; }
    echo "[$n] supervise:"
    st=$(_proc_stale "$n")
    case "$st" in
      pkg-stale|engine-stale|environment-stale) echo "  $n: $st -> auto-restart"; restart_one "$n" | sed 's/^/    /' || failed=1 ;;
      stopped)                echo "  $n: stopped (use 'start' to launch)" ;;
      *)                      echo "  $n: $st (no restart needed)" ;;
    esac
  done
  return "$failed"
}

cmd_config() {
  echo "resolved validated deployment config"
  echo "platform pkgs come from the declaration; a target manifest is the fallback when it names none"
  echo "per-repo (HOST | PKGSRC | DURABLE | local pkgs | platform pkgs):"
  local n package_source package_source_status
  for n in $DEPLOYMENT_OPERATOR_DEPLOYMENTS; do
    if cfg "$n" && derive_devloop_pkgs_from_workspace "$n" 2>/dev/null; then
      printf '  %-9s %s | %s | %s | %s | %s\n' "$n" "$HOST" "$PKGSRC" "$DUR" "${LOCAL_PKGS:--}" "$DEVLOOP_PKGS"
      package_source_status=$(package_source_config_status) || package_source_status="CONFIG-ERROR"
      while IFS= read -r package_source; do
        [ -n "$package_source" ] && printf '    package-source %s\n' "$package_source"
      done <<<"$package_source_status"
    else
      printf '  %-9s %s | %s | %s | %s | %s\n' "$n" "$HOST" "$PKGSRC" "$DUR" "${LOCAL_PKGS:--}" "CONFIG-ERROR"
    fi
  done
}

# When sourced (e.g. by scripts/deployment_operator_reaper_test.py) define functions only — skip the CLI dispatch.
[ "${BASH_SOURCE[0]}" = "${0}" ] || return 0 2>/dev/null || true
cmd="${1:-status}"; arg2="${2:-}"; arg3="${3:-}"
case "$cmd" in
  bin)     rc=0; for n in $(expand "${arg2:-all}"); do cfg "$n" && bin_ensure_fresh || rc=1; done; exit "$rc" ;;
  start)   rc=0; for n in $(expand "${arg2:-all}"); do start_one "$n" || rc=1; done; exit "$rc" ;;
  stop)    rc=0; for n in $(expand "${arg2:-all}"); do stop_one "$n" || rc=1; done; exit "$rc" ;;
  restart) rc=0; for n in $(expand "${arg2:-all}"); do restart_one "$n" || rc=1; done; exit "$rc" ;;
  sync)    cmd_sync "$arg2" ;;
  status)  for n in $(expand "${arg2:-all}"); do status_one "$n"; done ;;
  config)  cmd_config ;;
  board)   cmd_board "$arg2" "$arg3" ;;
  logs)    target="${arg2:-${DEPLOYMENT_OPERATOR_DEPLOYMENTS%% *}}"; cfg "$target" || exit 1; f=$(latest_log "$target"); echo "$f"; tail -"${arg3:-40}" "$f" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' ;;
  *) echo "usage: $0 {status|config|board|bin|start|stop|restart|sync|logs} [deployment-id|all] [stale_h|lines]"; exit 1 ;;
esac
