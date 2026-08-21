#!/usr/bin/env bash
set -euo pipefail

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=scripts/bin_bootstrap.sh
. "$script_root/scripts/bin_bootstrap.sh"

worktree="${FKST_DEVLOOP_CACHE_PREPARATION_WORKTREE:-}"
case "$worktree" in
  /*) ;;
  *) bootstrap_die "warm-pinned-bin-invalid-worktree: FKST_DEVLOOP_CACHE_PREPARATION_WORKTREE must be absolute" ;;
esac

# A repository that declares no substrate pin has nothing to warm. The engine source repository is
# itself the engine and carries no `.fkst/substrate-ref`, so treat absence as not-applicable rather than as a
# failure: erroring here fails the cache-preparation hook, which fails the whole implement attempt.
if [ ! -f "$worktree/.fkst/substrate-ref" ]; then
  printf 'warm-pinned-bin pin=none result=not-applicable\n'
  exit 0
fi

pin="$(bootstrap_read_pin "$worktree")"
bootstrap_result=""
bootstrap_bin_on_total_miss "$worktree" bootstrap_result >/dev/null
printf 'warm-pinned-bin pin=%s result=%s\n' "$pin" "$bootstrap_result"
