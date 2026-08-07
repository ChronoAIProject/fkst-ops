#!/usr/bin/env bash
# Operator-owned diagnostic sweep. Invocation timing is owned by the deployment.
set -uo pipefail

DOCTOR_FAILURES=0
DOCTOR_FINDINGS=0

doctor_failure() {
  DOCTOR_FAILURES=$((DOCTOR_FAILURES + 1))
  printf '  failure %s: %s\n' "$1" "$2"
}

doctor_targets() {
  [ -n "${FKST_OPS_DOCTOR_TARGETS:-}" ] || return 0
  printf '%s\n' "$FKST_OPS_DOCTOR_TARGETS"
}

fixture_process_row() {
  local pid="$1"
  awk -F '\t' -v pid="$pid" '$1 == pid && $7 == 1 { print; exit }' \
    "${FKST_OPS_DOCTOR_PROCESS_FIXTURE:?}"
}

process_ids_matching() {
  local pattern="$1"
  if [ -n "${FKST_OPS_DOCTOR_PROCESS_FIXTURE:-}" ]; then
    awk -F '\t' -v pattern="$pattern" '$7 == 1 && $6 ~ pattern { print $1 }' \
      "$FKST_OPS_DOCTOR_PROCESS_FIXTURE"
  else
    pgrep -f -- "$pattern" 2>/dev/null || true
  fi
}

process_field() {
  local pid="$1" field="$2" row ps_field
  if [ -n "${FKST_OPS_DOCTOR_PROCESS_FIXTURE:-}" ]; then
    row=$(fixture_process_row "$pid") || return 1
    printf '%s\n' "$row" | awk -F '\t' -v field="$field" '{print $field}'
  else
    case "$field" in
      2) ps_field="pgid";; 3) ps_field="ppid";; 4) ps_field="comm";;
      5) ps_field="etime";; 6) ps_field="command";; *) return 1;;
    esac
    ps -o "$ps_field=" -p "$pid" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
  fi
}

group_has_supervise() {
  local pgid="$1"
  if [ -n "${FKST_OPS_DOCTOR_PROCESS_FIXTURE:-}" ]; then
    awk -F '\t' -v pgid="$pgid" '$2 == pgid && $7 == 1 && $6 ~ /supervise --project-root/ { found=1 } END { exit !found }' \
      "$FKST_OPS_DOCTOR_PROCESS_FIXTURE"
  else
    pgrep -g "$pgid" -f -- 'supervise --project-root' >/dev/null 2>&1
  fi
}

kill_group() {
  local pgid="$1"
  if [ -n "${FKST_OPS_DOCTOR_PROCESS_FIXTURE:-}" ]; then
    local tmp="${FKST_OPS_DOCTOR_PROCESS_FIXTURE}.tmp.$$"
    awk -F '\t' -v OFS='\t' -v pgid="$pgid" '$2 == pgid {$7=0} {print}' \
      "$FKST_OPS_DOCTOR_PROCESS_FIXTURE" > "$tmp" && mv "$tmp" "$FKST_OPS_DOCTOR_PROCESS_FIXTURE"
  else
    kill -9 -"$pgid" 2>/dev/null
  fi
}

elapsed_seconds() {
  printf '%s\n' "$1" | awk -F '[:-]' '{n=NF;s=$n;m=$(n-1);h=(n>=3?$(n-2):0);d=(n>=4?$(n-3):0);print ((d*24+h)*60+m)*60+s}'
}

stray_supervise_report() {
  local managed="" identity project durable log_root pid cmd root stray=0
  while IFS=$'\t' read -r identity project durable log_root; do
    [ -n "$identity" ] || continue
    managed="${managed}${project}"$'\n'
  done < <(doctor_targets)
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    cmd=$(process_field "$pid" 6) || continue
    root=$(printf '%s\n' "$cmd" | sed -n 's/.*--project-root \([^ ]*\).*/\1/p')
    [ -n "$root" ] || continue
    if ! printf '%s' "$managed" | grep -qxF -- "$root"; then
      printf '  STRAY pid %s project-root %s - unmanaged supervise; may poison shared state\n' "$pid" "$root"
      stray=$((stray + 1))
    fi
  done < <(process_ids_matching 'supervise --project-root')
  [ "$stray" -eq 0 ] && echo "  none (every running supervise is a managed target)"
  DOCTOR_FINDINGS=$((DOCTOR_FINDINGS + stray))
  printf '  stray-supervise findings: %s\n' "$stray"
}

reap_leaked_test_procs() {
  local reap_min="${DOGFOOD_TEST_REAP_MINUTES:-45}" self_pgid pat pid pgid comm etime secs reaped=0 skipped=0
  local leader_comm leader_ppid
  self_pgid="${FKST_OPS_DOCTOR_SELF_PGID:-$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')}"
  for pat in 'fkst-framework test' 'scripts/[a-z0-9_]*_test\.py'; do
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      comm=$(process_field "$pid" 4) || continue
      case "$comm" in *node*|*codex*|*Code*) continue;; esac
      etime=$(process_field "$pid" 5) || continue
      secs=$(elapsed_seconds "$etime")
      [ "${secs:-0}" -gt "$((reap_min*60))" ] 2>/dev/null || continue
      pgid=$(process_field "$pid" 2) || continue
      [ -n "$pgid" ] || continue
      leader_comm=$(process_field "$pgid" 4) || leader_comm=""
      case "$leader_comm" in *node*|*codex*|*Code*)
        printf '  guarded-skip %s pid %s group %s age %ss - group leader comm=%s\n' "$pat" "$pid" "$pgid" "$secs" "$leader_comm"
        skipped=$((skipped + 1)); continue;;
      esac
      leader_ppid=$(process_field "$pgid" 3) || leader_ppid=""
      if [ "$leader_ppid" != "1" ]; then
        printf '  guarded-skip %s pid %s group %s age %ss - group leader has live parent %s\n' "$pat" "$pid" "$pgid" "$secs" "$leader_ppid"
        skipped=$((skipped + 1)); continue
      fi
      if [ "$pgid" = "$self_pgid" ] || group_has_supervise "$pgid"; then
        printf '  guarded-skip leaked %s pid %s age %ss - own/supervise group\n' "$pat" "$pid" "$secs"
        skipped=$((skipped + 1)); continue
      fi
      if [ "${DOGFOOD_REAP_DRYRUN:-0}" = "1" ]; then
        printf '  would-reap %s pid %s pgid %s age %ss\n' "$pat" "$pid" "$pgid" "$secs"
      else
        kill_group "$pgid" && printf '  reaped %s pid %s pgid %s age %ss\n' "$pat" "$pid" "$pgid" "$secs"
      fi
      reaped=$((reaped + 1))
    done < <(process_ids_matching "$pat")
  done
  DOCTOR_FINDINGS=$((DOCTOR_FINDINGS + reaped + skipped))
  printf '  leaked-test-proc reaper: %s reaped, %s guarded-skip (threshold %smin, orphaned-only)\n' "$reaped" "$skipped" "$reap_min"
}

file_mtime() {
  stat -f '%m' "$1" 2>/dev/null || stat -c '%Y' "$1" 2>/dev/null
}

sweep_stale_tmp_receipts() {
  local hours="${DOGFOOD_RECEIPT_SWEEP_HOURS:-6}" root="${DOGFOOD_RECEIPT_SWEEP_ROOT:-/tmp}" swept=0 preserved=0 f mtime
  local now="${FKST_OPS_DOCTOR_NOW_EPOCH:-$(date +%s)}" threshold=$((hours*3600))
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    mtime=$(file_mtime "$f") || continue
    if [ "$((now-mtime))" -gt "$threshold" ]; then
      if [ "${DOGFOOD_RECEIPT_SWEEP_DRYRUN:-0}" = "1" ]; then
        printf '  would-sweep %s\n' "$f"
      else
        rm -f "$f" 2>/dev/null && printf '  swept %s\n' "$f"
      fi
      swept=$((swept + 1))
    else
      printf '  preserved %s\n' "$f"
      preserved=$((preserved + 1))
    fi
  done < <(find "$root" -maxdepth 1 -type f \( -name 'fkst-github-proxy-*' -o -name 'fkst-github-devloop-dashboard-*' \) -print 2>/dev/null | LC_ALL=C sort)
  DOCTOR_FINDINGS=$((DOCTOR_FINDINGS + swept))
  printf '  stale-tmp-receipt sweep: %s reaped, %s preserved (>%sh)\n' "$swept" "$preserved" "$hours"
}

durable_health_one() {
  local identity="$1" durable="$2" log_root="$3" snapshot summary now_ms
  if [ ! -e "$durable/delivery.redb" ]; then echo "  $identity: no durable store"; return 0; fi
  now_ms=$(( ${FKST_OPS_DOCTOR_NOW_EPOCH:-$(date +%s)} * 1000 ))
  if ! snapshot=$("${FKST_OPS_ENGINE_BINARY:?FKST_OPS_ENGINE_BINARY is required}" observe --json --durable-root "$durable" 2>&1); then
    doctor_failure "durable.$identity" "observe operation failed: $(printf '%s' "$snapshot" | sed -n '1p')"
    return 0
  fi
  summary=$(printf '%s' "$snapshot" | jq -r --argjson now "$now_ms" '
    ([.queues[].pending]|add // 0) as $p |
    (([.queues[].oldest_pending_age_ms]|max // 0)/3600000|floor) as $oh |
    (.dead_letters|length) as $dl_total |
    ([.dead_letters[] | select(($now - (.dead_at_ms // 0)) <= 21600000)] | length) as $dl_recent |
    (.truncated.dead_letters // false) as $truncated |
    "\(.queues|length) queues, \($p) pending (oldest \($oh)h), \($dl_recent) dead-letters<6h (\($dl_total) total)" +
      (if ($dl_recent>0 or $oh>6 or $truncated) then " WARNING" else "" end)' 2>/dev/null) || true
  if [ -z "$summary" ]; then
    doctor_failure "durable.$identity" "observe returned malformed JSON"
  else
    printf '  %s: %s\n' "$identity" "$summary"
  fi
}

durable_health_report() {
  local identity project durable log_root
  while IFS=$'\t' read -r identity project durable log_root; do
    [ -n "$identity" ] || continue
    durable_health_one "$identity" "$durable" "$log_root"
  done < <(doctor_targets)
}

doctor_main() {
  [ "$#" -eq 0 ] || { echo "usage: doctor/doctor.sh" >&2; return 2; }
  echo "stray supervises (unmanaged):"; stray_supervise_report
  echo "leaked test-proc reaper:"; reap_leaked_test_procs
  echo "stale temporary receipt sweep:"; sweep_stale_tmp_receipts
  echo "durable delivery state:"; durable_health_report
  printf 'doctor accounting: %s findings, %s failures\n' "$DOCTOR_FINDINGS" "$DOCTOR_FAILURES"
  [ "$DOCTOR_FAILURES" -eq 0 ]
}

[ "${BASH_SOURCE[0]}" = "$0" ] || return 0 2>/dev/null || true
doctor_main "$@"
