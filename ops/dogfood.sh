#!/usr/bin/env bash
# dogfood.sh — single operator multi-tool for dogfooding github-devloop on this device.
#
# Each deployment drives one target's issue-to-change loop with declared target,
# platform, and engine sources and real write posture (FKST_GITHUB_WRITE=1).
#
# Package layout: `.fkst/` is RUNTIME/build only (gitignored: runtime, durable,
# substrate-src, board cache) except host repos that intentionally commit their own
# local package source under `.fkst/local-packages/<pkg>`. The engine BIN + shared
# devloop packages are the PLATFORM, loaded by delegating the resolved topology to
# the host-run contract in `$PKGSRC/scripts/run.sh supervise`.
#
# Commands:
#   ./dogfood.sh status  [name|all]            pid/uptime/code-version/panic per supervise
#   ./dogfood.sh board   [name|all] [stale_h]  GitHub board sweep: which issues/PRs flow vs are stuck (default stale 6h)
#   ./dogfood.sh bin                           ensure engine BIN == substrate origin/dev; rebuild if stale (no restart)
#   ./dogfood.sh start   [name|all]            launch via host-run contract
#   ./dogfood.sh stop    [name|all]            SIGKILL (releases the redb lock)
#   ./dogfood.sh restart [name|all]            sync run checkouts to origin/<integration> + relaunch (unconditional)
#   ./dogfood.sh sync    [name|all]            auto-deploy: ff pinned operator checkouts to dev, rebuild BIN,
#                                              and restart ONLY supervises whose running code is a real
#                                              package/engine change (skill/docs-only skew is left running)
#   ./dogfood.sh logs    [name] [lines]        tail the latest log (default first declaration, 40 lines)
#
# Dogfood resolves per-machine topology and delegates one-host launch invariants
# (fresh runtime scratch, stable durable reuse, package loading, and restart) to
# `scripts/run.sh supervise`.
set -uo pipefail

# ---- validated deployment input ----
_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_repo_root="$(git -C "$_self_dir" rev-parse --show-toplevel 2>/dev/null || true)"
: "${FKST_OPS_DECLARATION:?FKST_OPS_DECLARATION is required}"
: "${FKST_OPS_MACHINE_PROFILE:?FKST_OPS_MACHINE_PROFILE is required}"
: "${FKST_OPS_LOCK:?FKST_OPS_LOCK is required}"
RESOLVED_DECLARATION="$(PYTHONPATH="$_repo_root${PYTHONPATH:+:$PYTHONPATH}" python3 -m schema.validator \
  "$FKST_OPS_DECLARATION" "$FKST_OPS_MACHINE_PROFILE" "$FKST_OPS_LOCK")" || exit $?
DOGFOOD_REPOS="$(printf '%s' "$RESOLVED_DECLARATION" | python3 -c \
  'import json,sys; print(" ".join(item["id"] for item in json.load(sys.stdin)["deployment"]))')"

# The shared devloop family = the PLATFORM (like GitHub runners + marketplace actions), loaded from the
# platform checkout's repo-root packages/ (PKGSRC). Each target-source-
# primary TARGET repo (host) commits its OWN custom Lua packages under `.fkst/local-packages/<pkg>`
# (root stays website source) — so platform packages come from `$PKGSRC/packages/<pkg>`, a host's own package
# from `$HOST/.fkst/local-packages/<pkg>`. (`.fkst/` is a tracked+ignored runtime INTERFACE dir, not
# "all runtime": host repos may commit their own Lua there.)
# Platform packages every dogfood supervise LOADS + RUNS from PKGSRC/packages/ are selected by the
# target host's `fkst.workspace.toml`. Non-self hosts use
# the declared platform external source's packages; the self host uses explicit workspace
# `[[package]]` entries. `dogfood.sh` only derives the launch argument from that manifest and never
# rewrites it, so a drift between committed composition and launch composition fails closed in the
# host-run contract instead of being masked.
DEVLOOP_PKGS=""

# cfg <id> consumes only the schema validator's resolved output.
cfg() {
  local values
  values="$(printf '%s' "$RESOLVED_DECLARATION" | python3 -c '
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
fields=[dep["target_identity"],m["target_checkout"],m["platform_checkout"],m["engine_checkout"],m["engine_binary"],m["durable"],m["runtime"],m["logs"],m.get("rate_pool", empty),m.get("bot_login", empty),json.dumps(m.get("managed_bot_set", []),separators=(",",":")),dep["integration"]["upstream_branch"],dep["integration"]["integration_branch"],dep["integration"]["rollup_merge"]," ".join(dep["packages"]["host"]) or empty,json.dumps(profile,separators=(",",":")),dep["sources"]["platform"]["lock_ref"],dep["sources"]["target"]["git"],dep["sources"]["platform"]["git"],*provider("engine"),*provider("board_engine_durable"),*provider("board_github_control")]
print("\t".join(fields))
' "$1")" || { echo "unknown deployment: $1" >&2; return 1; }
  IFS=$'\t' read -r REPO HOST PKGSRC SUBSTRATE_SRC BIN DUR RUNTIME_ROOT LOGDIR RATE_POOL BOT MANAGED_BOT_LOGINS UPSTREAM_BRANCH INTEGRATION_BRANCH ROLLUP_MERGE LOCAL_PKGS GITHUB_DEVLOOP_PROFILE PLATFORM_SOURCE_ID TARGET_GIT_URL PLATFORM_GIT_URL ENGINE_PROVIDER ENGINE_CONTRACT ENGINE_PROVIDER_CONFIGURATION ENGINE_BOARD_PROVIDER ENGINE_BOARD_CONTRACT ENGINE_BOARD_PROVIDER_CONFIGURATION GITHUB_BOARD_PROVIDER GITHUB_BOARD_CONTRACT GITHUB_BOARD_PROVIDER_CONFIGURATION <<<"$values"
  [ "$RATE_POOL" = "__FKST_OPS_EMPTY__" ] && RATE_POOL=""
  [ "$BOT" = "__FKST_OPS_EMPTY__" ] && BOT=""
  [ "$LOCAL_PKGS" = "__FKST_OPS_EMPTY__" ] && LOCAL_PKGS=""
}

derive_devloop_pkgs_from_workspace() { # $1 name
  local name="$1" output
  output="$(python3 "$_self_dir/workspace_manifest.py" platform-packages "$name" "$HOST" "$PKGSRC" "$PLATFORM_SOURCE_ID")" \
    || { printf '%s\n' "$output" >&2; return 1; }
  DEVLOOP_PKGS="$output"
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
expand() { [ "${1:-all}" = all ] && echo "$DOGFOOD_REPOS" || echo "$1"; }

invoke_provider() { python3 "$_self_dir/invoke_provider.py" "$1" "$2"; }

# Sync a dogfood RUN checkout (behavior PKGSRC + target HOST) to the machine's
# INTEGRATION_BRANCH — the dogfood runs its own pre-rollup code (feature ->
# integration-<device> -> rollup -> dev), so it is the live integration test of
# this device's autonomous changes BEFORE they promote to dev. The rollup target

ensure_run_checkout() { # $1 checkout dir, $2 Git URL
  local dir="$1" git_url="$2" corrupt=""
  if ! git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
    corrupt="not-a-git-repo"
  elif git -C "$dir" status --porcelain 2>/dev/null | grep -q '^ D '; then
    corrupt="deleted-tracked-files"
  fi
  [ -z "$corrupt" ] && return 0
  echo "  ! run checkout $dir corrupt ($corrupt; likely runtime-root cleanup) -> re-cloning $git_url"
  [ -e "$dir" ] && mv "$dir" "${dir}.corrupt.$(date +%s)" 2>/dev/null
  mkdir -p "$(dirname "$dir")"
  git clone -q "$git_url" "$dir" \
    && echo "    re-cloned $git_url -> $dir" \
    || { echo "    ERROR: failed to clone $git_url into $dir"; return 1; }
}

restore_generated_workspace_scratch() { # $1 worktree dir
  local wt="$1"
  [ -f "$wt/fkst.workspace.toml" ] || return 0
  git -C "$wt" diff --quiet -- fkst.workspace.toml 2>/dev/null && return 0
  python3 "$_self_dir/workspace_manifest.py" is-generated-scratch "$wt" "$DEVLOOP_PKGS" "$PLATFORM_SOURCE_ID" >/dev/null || return 0
  echo "    restoring generated fkst.workspace.toml scratch before branch sync"
  git -C "$wt" checkout -q -- fkst.workspace.toml 2>/dev/null
}

sync_to_run_branch() { # $1 worktree dir
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1 || { echo "  ! $1 is not a git worktree"; return 1; }
  git -C "$1" fetch origin "$INTEGRATION_BRANCH" -q 2>/dev/null
  local target; target=$(git -C "$1" rev-parse --short "origin/$INTEGRATION_BRANCH" 2>/dev/null)
  # checkout -B (not reset --hard): leaves the checkout actually ON the integration branch
  # tracking origin/<integration>, instead of pointing a stale local 'dev' ref at integration content.
  local note; note=$(git -C "$1" checkout -q -B "$INTEGRATION_BRANCH" "origin/$INTEGRATION_BRANCH" 2>&1 | tail -1)
  # Verify the checkout actually REACHED target, then self-heal. A checkout that aborts (working-tree
  # obstruction, a file<->symlink/dir transition racing the running supervise, a dirty tree) otherwise
  # leaves the clone on STALE code while the function returns ok and the supervise silently launches
  # stale — the exact "supervise silently re-running already-fixed defects" failure this tooling exists
  # to prevent. Self-heal forcefully (reset --hard + clean reaches the fetched ref regardless of the
  # obstruction; clean -fd keeps gitignored .fkst/ runtime), then re-assert the branch so the checkout
  # stays ON <integration>. If it STILL cannot reach target (deep corruption ensure_run_checkout should
  # have re-cloned), fail loud with STALE-CHECKOUT so the operator and doctor (pkg-stale) catch it.
  if [ -n "$target" ] && [ "$(git -C "$1" rev-parse --short HEAD 2>/dev/null)" != "$target" ]; then
    git -C "$1" reset --hard "origin/$INTEGRATION_BRANCH" -q 2>/dev/null
    git -C "$1" clean -fdq 2>/dev/null
    git -C "$1" checkout -q -B "$INTEGRATION_BRANCH" "origin/$INTEGRATION_BRANCH" 2>/dev/null
    note="self-healed stale checkout (was: ${note:-checkout-failed})"
  fi
  local head; head=$(git -C "$1" rev-parse --short HEAD 2>/dev/null)
  if [ -n "$target" ] && [ "$head" != "$target" ]; then
    echo "  $1 -> STALE-CHECKOUT: still $head, target $target ($note) ($INTEGRATION_BRANCH)"
    return 1
  fi
  echo "  $1 -> $head${note:+ ($note)} ($INTEGRATION_BRANCH)"
}

# Ensure a checkout's INTEGRATION_BRANCH is >= UPSTREAM_BRANCH (dev) by merging upstream
# FORWARD into integration and pushing. Why: operator out-of-band fixes land on dev; the
# dogfood runs on integration; the in-pipeline sync_scan ff's dev->integration but can lag
# (or the running supervise is itself stale), so _proc_stale reads "current" against a stale
# integration and the supervise never picks up operator fixes. This deterministically merges
# dev forward (plain ff when integration is an ancestor of dev; a merge commit when integration
# has its own un-rolled commits — both keep integration >= dev) and pushes, so the next
# _proc_stale sees pkg-stale and restarts onto the fix. Forward-only (never rewrites integration);
# aborts on conflict and leaves it for sync_conflict; a push failure is non-fatal.
ensure_integration_caught_up() { # $1 checkout dir
  local wt="$1"
  git -C "$wt" rev-parse --git-dir >/dev/null 2>&1 || return 0
  [ "$INTEGRATION_BRANCH" = "$UPSTREAM_BRANCH" ] && return 0   # single-branch topology: nothing to merge
  git -C "$wt" fetch origin "$INTEGRATION_BRANCH" "$UPSTREAM_BRANCH" -q 2>/dev/null || return 0
  git -C "$wt" rev-parse --verify "origin/$INTEGRATION_BRANCH" >/dev/null 2>&1 || return 0
  git -C "$wt" rev-parse --verify "origin/$UPSTREAM_BRANCH"   >/dev/null 2>&1 || return 0
  local behind; behind=$(git -C "$wt" rev-list --count "origin/$INTEGRATION_BRANCH..origin/$UPSTREAM_BRANCH" 2>/dev/null || echo 0)
  [ "${behind:-0}" -eq 0 ] && return 0
  echo "  $INTEGRATION_BRANCH is $behind behind $UPSTREAM_BRANCH in $(basename "$wt") -> merging $UPSTREAM_BRANCH forward"
  restore_generated_workspace_scratch "$wt"
  git -C "$wt" checkout -q -B "$INTEGRATION_BRANCH" "origin/$INTEGRATION_BRANCH" 2>/dev/null \
    || { echo "    WARN: could not checkout $INTEGRATION_BRANCH — leaving for sync_scan"; return 0; }
  restore_generated_workspace_scratch "$wt"
  if git -C "$wt" merge --no-edit "origin/$UPSTREAM_BRANCH" >/dev/null 2>&1; then
    if git -C "$wt" push origin "HEAD:$INTEGRATION_BRANCH" >/dev/null 2>&1; then
      echo "    merged + pushed: $INTEGRATION_BRANCH -> $(git -C "$wt" rev-parse --short HEAD)"
    else
      echo "    WARN: merge ok but push failed (perm/race) — leaving for sync_scan"
    fi
  else
    git -C "$wt" merge --abort 2>/dev/null
    echo "    WARN: $UPSTREAM_BRANCH does not merge cleanly into $INTEGRATION_BRANCH — leaving for sync_conflict"
  fi
}

engine_build_result() {
  # engine-provider-configuration: forward committed binding configuration as typed input.
  python3 -c 'import json,sys; c=json.loads(sys.argv[4]); print(json.dumps({"engine_checkout":sys.argv[1],"engine_binary":sys.argv[2],"expected_branch":sys.argv[3],"operation":"build","build_command":c["build_command"]}))' "$SUBSTRATE_SRC" "$BIN" "$UPSTREAM_BRANCH" "$ENGINE_PROVIDER_CONFIGURATION" \
    | invoke_provider "$ENGINE_PROVIDER" "$ENGINE_CONTRACT" || return $?
}

bin_ensure_fresh() {
  local response
  response=$(engine_build_result) || return $?
  printf '%s\n' "$response" | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print("built: %s@%s" % (r["binary"],r["source_rev"][:8]))'
}

cmd_board() {
  local target="${1:-all}" n failed=0 tmp github_input engine_input
  for n in $(expand "$target"); do
    cfg "$n" || { failed=1; continue; }
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/fkst-ops-board.XXXXXX") || return 1
    github_input="$tmp/github.json"; engine_input="$tmp/engine.json"
    python3 -c 'import json,sys; p=json.loads(sys.argv[3]); p["stale_hours"]=int(sys.argv[6] or 6); json.dump({"target_identity":sys.argv[1],"platform_checkout":sys.argv[2],"profile":p,"bot_login":sys.argv[4],"managed_bot_set":json.loads(sys.argv[5])},open(sys.argv[7],"w"))' "$REPO" "$PKGSRC" "$GITHUB_DEVLOOP_PROFILE" "$BOT" "$MANAGED_BOT_LOGINS" "${2:-}" "$github_input"
    python3 -c 'import json,sys; json.dump({"engine_binary":sys.argv[1],"durable_root":sys.argv[2],"cache":sys.argv[3],"refresh":False,"ttl_seconds":300,"stall_seconds":900},open(sys.argv[4],"w"))' "$BIN" "$DUR" "$tmp/cache.json" "$engine_input"
    python3 "$_repo_root/board/board.py" --github-provider "$GITHUB_BOARD_PROVIDER" --engine-provider "$ENGINE_BOARD_PROVIDER" --github-input "$github_input" --engine-input "$engine_input" || failed=1
    rm -rf "$tmp"
  done
  return "$failed"
}

# Prune worktrees + scratch dirs from OLD runtime roots of this dogfood (implement/fix
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
  if ! command -v lsof >/dev/null 2>&1; then
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
    writer_census=$(lsof +D "$d" 2>&1) || writer_census_status=$?
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
    python3 "$_self_dir/dead_letter_causes.py" archive \
      --runtime-root "$d" --output "$LOGDIR/${name}-dead-letter-facts.log" \
      || { echo "[$name] could not retain dead-letter cause facts from $d" >&2; return 1; }
    rm -rf "$d" 2>/dev/null
  done
}

launch_one() { # $1 name, $2 restart flag (0|1)
  local name="$1" restart="${2:-0}" ts log rt args=()
  ts=$(date +%s); log="$LOGDIR/${name}-sv-${ts}.log"; rt="$RUNTIME_ROOT/${name}.${ts}"
  derive_devloop_pkgs_from_workspace "$name" || return 1
  [ -n "$DEVLOOP_PKGS" ] || { echo "[$name] no platform packages declared in fkst.workspace.toml"; return 1; }
  [ -x "$PKGSRC/scripts/run.sh" ] || { echo "[$name] missing host-run contract: $PKGSRC/scripts/run.sh"; return 1; }

  args=(
    "$PKGSRC/scripts/run.sh" supervise
    --project-root "$HOST"
    --platform-root "$PKGSRC"
    --platform-packages "$DEVLOOP_PKGS"
    --durable-root "$DUR"
    --runtime-root "$rt"
  )
  [ -n "$LOCAL_PKGS" ] && args+=(--host-packages "$LOCAL_PKGS")
  [ "$restart" = "1" ] && args+=(--restart)

  # Own-session launch: make the supervise its OWN session/process-group leader. CONFIRMED (ps): the
  # plain `nohup "${args[@]}" &` launch left the supervise in the LAUNCHER's process group (PGID = the
  # launching shell's, not its own pid) — vulnerable to any group-directed signal to that pgroup
  # (`kill -- -<pgid>`). Closing that confirmed foreign-pgroup membership is the point of this change.
  # [ASSUMED-UNVERIFIED: the recurring out-of-band SIGTERM that forced manual restarts ~every few hours
  # is *inferred* to be such a group signal on launcher/session/background-task teardown — it was not
  # caught live. This hardens the confirmed vulnerability; it does NOT prove recurrence-elimination,
  # which must be observed after this lands.] `nohup` only blocks SIGHUP, not group signals. macOS has
  # no setsid(1), so wrap in python3 (already required by scripts/run.sh; perl was rejected — it panics
  # under the automation env's LC_ALL=C.UTF-8 locale). `os.setsid()`+`os.execvp` is IN-PLACE, so $!
  # below stays the REAL supervise pid and the env-prefix stays scoped to the launch; a failed setsid
  # raises OSError → nonzero exit → the readiness wait reports the launch failure loud (self-verifying).
  BIN="$BIN" FKST_GITHUB_REPO="$REPO" FKST_GITHUB_WRITE=1 \
    FKST_DEVLOOP_UPSTREAM_BRANCH="$UPSTREAM_BRANCH" FKST_DEVLOOP_INTEGRATION_BRANCH="$INTEGRATION_BRANCH" \
    FKST_DEVLOOP_ROLLUP_MERGE="$ROLLUP_MERGE" FKST_OPS_GITHUB_DEVLOOP_PROFILE="$GITHUB_DEVLOOP_PROFILE" \
    FKST_OPS_PROFILE_MACHINE="$(printf '{\"rate_pool\":%s,\"bot_login\":%s,\"managed_bot_set\":%s}' \
      "$(printf '%s' "$RATE_POOL" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
      "$(printf '%s' "$BOT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" "$MANAGED_BOT_LOGINS")" \
    FKST_WORKTREE_GC_REMOVE=1 \
    nohup python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "${args[@]}" > "$log" 2>&1 &
  local pid=$!
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
# Deliberately NOT named launch_one: that name carries the scripts/run.sh supervise delegation that
# G-DOGFOOD-BOUNDARY audits, and this wrapper must not displace it from the audited surface.
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
  kill -9 $p 2>/dev/null; echo "[$1] killed $p"
}

restart_one() {
  cfg "$1" || return 1
  echo "[$1] sync to origin/$INTEGRATION_BRANCH (run branch; rollup target stays $UPSTREAM_BRANCH):"
  ensure_run_checkout "$PKGSRC" "$PLATFORM_GIT_URL" || return 1
  if [ "$HOST" != "$PKGSRC" ]; then
    ensure_run_checkout "$HOST" "$TARGET_GIT_URL" || return 1
  fi
  derive_devloop_pkgs_from_workspace "$1" || return 1
  ensure_integration_caught_up "$PKGSRC"                              # keep run branch (integration) >= dev so operator fixes deploy
  [ "$HOST" != "$PKGSRC" ] && ensure_integration_caught_up "$HOST"
  sync_to_run_branch "$PKGSRC"
  [ "$HOST" != "$PKGSRC" ] && sync_to_run_branch "$HOST"
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

status_one() {
  cfg "$1" || return 1
  local p log; p=$(pidof_df); log=$(latest_log "$1")
  if [ -z "$p" ]; then echo "[$1] STOPPED   (target $REPO)"; return 0; fi
  local et panic last hv pv
  et=$(fmt_uptime "$(ps -o etime= -p $p 2>/dev/null | tr -d ' ')")
  panic=$(engine_panic_count "$log")
  last=$(tail -1 "$log" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-44)
  hv=$(git -C "$HOST" rev-parse HEAD 2>/dev/null | cut -c1-8)
  pv=$(git -C "$PKGSRC" rev-parse HEAD 2>/dev/null | cut -c1-8)
  printf '[%s] RUNNING pid %s up %s panic=%s | host@%s pkgs@%s | %s\n' "$1" "$p" "$et" "$panic" "$hv" "$pv" "$last"
}

# _proc_stale <name> -> freshness verdict of the RUNNING process vs origin/dev. Authoritative =
# the code the process loaded at startup (logged code_provenance PKG_VERS/ENGINE_VER), NOT the
# worktree/BIN file (those can be updated without reloading the process — only a restart reloads).
# Echoes: stopped | current | skew (dev moved, non-package files only) | pkg-stale | engine-stale.
# PKG freshness is vs PKGSRC origin/$INTEGRATION_BRANCH (the run branch the dogfood loads);
# ENGINE freshness is the revision returned by the declared engine build provider.
_proc_stale() {
  cfg "$1" || { echo unknown; return; }
  local p log procpkg proceng pdev sdev; p=$(pidof_df); log=$(latest_log "$1")
  [ -z "$p" ] && { echo stopped; return; }
  derive_devloop_pkgs_from_workspace "$1" >/dev/null || { echo config-error; return; }
  git -C "$PKGSRC" fetch origin "$INTEGRATION_BRANCH" -q 2>/dev/null
  pdev=$(git -C "$PKGSRC" rev-parse "origin/$INTEGRATION_BRANCH" 2>/dev/null)
  sdev=$(engine_build_result | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["source_rev"])') || { echo engine-provider-failed; return; }
  procpkg=$(grep -aoE "${DEVLOOP_PKGS%% *}@[a-f0-9]+" "$log" 2>/dev/null | tail -1 | cut -d@ -f2)   # any platform pkg's commit reflects the running code
  proceng=$(grep -aoE 'ENGINE_VER=[a-f0-9]+' "$log" 2>/dev/null | tail -1 | cut -d= -f2)
  if [ -n "$proceng" ] && [ "${sdev:0:${#proceng}}" != "$proceng" ]; then echo engine-stale; return; fi
  if [ -n "$procpkg" ] && [ "${pdev:0:${#procpkg}}" != "$procpkg" ]; then
    if [ -n "$(git -C "$PKGSRC" diff "$procpkg" "$pdev" -- packages/ 2>/dev/null)" ]; then echo pkg-stale; else echo skew; fi
    return
  fi
  echo current
}

_sync_checkout() {
  local co="$1" before after
  { [ -n "$co" ] && git -C "$co" rev-parse --git-dir >/dev/null 2>&1; } || { echo "  ${co:-?}: not a git checkout (skip)"; return; }
  git -C "$co" fetch origin "$UPSTREAM_BRANCH" -q 2>/dev/null
  before=$(git -C "$co" rev-parse --short HEAD 2>/dev/null)
  if [ -n "$(git -C "$co" status --porcelain 2>/dev/null)" ]; then
    echo "  $co: DIRTY — not synced (a pinned checkout must be clean; make changes in a worktree)"; return
  fi
  if ! git -C "$co" merge-base --is-ancestor HEAD "origin/$UPSTREAM_BRANCH" 2>/dev/null; then
    echo "  $co: $before not an ancestor of origin/$UPSTREAM_BRANCH — skip (feature branch / diverged; not a pinned dev mirror)"; return
  fi
  # Verify the end state. A failed reset leaves HEAD unmoved, which otherwise looks
  # identical to an already-current checkout when only before and after are compared.
  local reset_err reset_rc target
  reset_err=$(git -C "$co" reset -q --hard "origin/$UPSTREAM_BRANCH" 2>&1); reset_rc=$?
  after=$(git -C "$co" rev-parse --short HEAD 2>/dev/null)
  target=$(git -C "$co" rev-parse --short "origin/$UPSTREAM_BRANCH" 2>/dev/null)
  if [ "$reset_rc" -ne 0 ] || [ "$after" != "$target" ]; then
    echo "  $co: SYNC FAILED -- still at $after, origin/$UPSTREAM_BRANCH is $target (rc=$reset_rc)${reset_err:+ -- $reset_err}"
    echo "  $co: the pinned checkout is STALE; skill/tooling loaded from it may be out of date"
    return 1
  fi
  [ "$before" = "$after" ] && echo "  $co: current ($after)" || echo "  $co: $before -> $after"
}

# cmd_sync: keep everything current in one call. Fast-forward the pinned operator checkouts (the one
# this skill+dogfood.sh load from, and the substrate BIN source) to origin/dev, rebuild the BIN if
# the engine moved, then AUTO-RESTART only the supervises whose RUNNING code is a real package or
# engine change (pkg-stale/engine-stale). Skill/docs-only skew and already-current processes are
# left running — a restart would only churn in-flight codex for no code change.
cmd_sync() {
  echo "operator checkouts -> origin/$UPSTREAM_BRANCH:"
  local co_failed=0
  _sync_checkout "$(git -C "$_self_dir" rev-parse --show-toplevel 2>/dev/null)" || co_failed=1  # repo this skill lives in
  echo "engine BIN:"; bin_ensure_fresh | sed 's/^/  /'
  echo "supervises (auto-restart only on real code change):"
  local n st failed=0
  for n in $(expand "${1:-all}"); do
    cfg "$n" || continue
    derive_devloop_pkgs_from_workspace "$n" || { echo "  $n: config-error"; failed=1; continue; }
    ensure_integration_caught_up "$PKGSRC"                              # keep run branch (integration) >= dev so operator fixes deploy
    [ "$HOST" != "$PKGSRC" ] && ensure_integration_caught_up "$HOST"
    st=$(_proc_stale "$n")
    case "$st" in
      pkg-stale|engine-stale) echo "  $n: $st -> auto-restart"; restart_one "$n" | sed 's/^/    /' || failed=1 ;;
      stopped)                echo "  $n: stopped (use 'start' to launch)" ;;
      *)                      echo "  $n: $st (no restart needed)" ;;
    esac
  done
  [ "$co_failed" -eq 0 ] || failed=1
  return "$failed"
}

cmd_config() {
  echo "resolved validated deployment config"
  echo "platform pkgs resolve per repo from fkst.workspace.toml"
  echo "per-repo (HOST | PKGSRC | DURABLE | local pkgs | platform pkgs):"
  local n
  for n in $DOGFOOD_REPOS; do
    if cfg "$n" && derive_devloop_pkgs_from_workspace "$n" 2>/dev/null; then
      printf '  %-9s %s | %s | %s | %s | %s\n' "$n" "$HOST" "$PKGSRC" "$DUR" "${LOCAL_PKGS:--}" "$DEVLOOP_PKGS"
    else
      printf '  %-9s %s | %s | %s | %s | %s\n' "$n" "$HOST" "$PKGSRC" "$DUR" "${LOCAL_PKGS:--}" "CONFIG-ERROR"
    fi
  done
}

# When sourced (e.g. by scripts/dogfood_reaper_test.py) define functions only — skip the CLI dispatch.
[ "${BASH_SOURCE[0]}" = "${0}" ] || return 0 2>/dev/null || true
cmd="${1:-status}"; arg2="${2:-}"; arg3="${3:-}"
case "$cmd" in
  bin)     bin_ensure_fresh ;;
  start)   rc=0; for n in $(expand "${arg2:-all}"); do start_one "$n" || rc=1; done; exit "$rc" ;;
  stop)    for n in $(expand "${arg2:-all}"); do stop_one "$n"; done ;;
  restart) rc=0; for n in $(expand "${arg2:-all}"); do restart_one "$n" || rc=1; done; exit "$rc" ;;
  sync)    cmd_sync "$arg2" ;;
  status)  for n in $(expand "${arg2:-all}"); do status_one "$n"; done ;;
  config)  cmd_config ;;
  board)   cmd_board "$arg2" "$arg3" ;;
  logs)    target="${arg2:-${DOGFOOD_REPOS%% *}}"; cfg "$target" || exit 1; f=$(latest_log "$target"); echo "$f"; tail -"${arg3:-40}" "$f" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' ;;
  *) echo "usage: $0 {status|config|board|bin|start|stop|restart|sync|logs} [deployment-id|all] [stale_h|lines]"; exit 1 ;;
esac
