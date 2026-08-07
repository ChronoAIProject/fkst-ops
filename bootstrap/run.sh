#!/usr/bin/env bash
# Deployment-owned template: hydrate and delegate to the repository-pinned fkst-ops.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="${FKST_OPS_LOCK_FILE:-$ROOT/fkst.lock}"
CACHE_ROOT="${FKST_OPS_CACHE_ROOT:-$ROOT/.fkst/run/fkst-ops}"
TREE_HASHER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/canonical_tree.py"

read_pin() {
  python3 - "$LOCK_FILE" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
try:
    lock = tomllib.loads(path.read_text(encoding="utf-8"))
except (OSError, tomllib.TOMLDecodeError) as exc:
    print(f"error: cannot read fkst lockfile {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
sources = lock.get("external_source", [])
if isinstance(sources, dict):
    sources = [sources]
for source in sources:
    if source.get("id") != "fkst-ops":
        continue
    revision = source.get("resolved", {}).get("rev")
    tree = source.get("resolved", {}).get("tree_sha256")
    url = source.get("git")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        print("error: fkst-ops lock entry needs resolved.rev as a full lowercase git SHA", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(tree, str) or not re.fullmatch(r"sha256-[0-9a-f]{64}", tree):
        print("error: fkst-ops lock entry needs canonical resolved.tree_sha256", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(url, str) or not url or "\t" in url or "\n" in url:
        print("error: fkst-ops lock entry needs git", file=sys.stderr)
        raise SystemExit(1)
    print(url, revision, tree, sep="\t")
    raise SystemExit(0)
print("error: fkst.lock is missing external_source(id=fkst-ops)", file=sys.stderr)
raise SystemExit(1)
PY
}

verify_checkout() {
  local checkout="$1" revision="$2" expected_tree="$3" head actual_tree
  head="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  [ "$head" = "$revision" ] || { echo "error: fkst-ops resolved.rev mismatch: expected $revision, got ${head:-missing}" >&2; return 1; }
  actual_tree="$(python3 "$TREE_HASHER" "$checkout" "$revision")"
  [ "$actual_tree" = "$expected_tree" ] || { echo "error: fkst-ops tree_sha256 mismatch: expected $expected_tree, got $actual_tree" >&2; return 1; }
}

usage() {
  echo "usage: bootstrap/run.sh <declaration-path> <machine-resolution-args...>" >&2
}

[ "$#" -ge 1 ] || { usage; exit 2; }
declaration="$1"
shift
IFS=$'\t' read -r repo_url revision expected_tree < <(read_pin)

mkdir -p "$CACHE_ROOT/checkouts"
current="$CACHE_ROOT/current"
candidate=""
cleanup() { [ -z "$candidate" ] || rm -rf "$candidate"; }
trap cleanup EXIT

if [ -L "$current" ] && cached="$(readlink "$current")" && [ -d "$cached/.git" ]; then
  verify_checkout "$cached" "$revision" "$expected_tree"
  "$cached/bin/fkst-ops" preflight --declaration "$declaration" "$@"
  exec "$cached/bin/fkst-ops" --declaration "$declaration" "$@"
fi

candidate="$(mktemp -d "$CACHE_ROOT/checkouts/.partial.XXXXXX")"
git clone --quiet --no-checkout "$repo_url" "$candidate"
git -C "$candidate" checkout --quiet "$revision"
verify_checkout "$candidate" "$revision" "$expected_tree"
"$candidate/bin/fkst-ops" preflight --declaration "$declaration" "$@"
verified="$CACHE_ROOT/checkouts/$revision"
if [ ! -e "$verified" ]; then
  mv "$candidate" "$verified"
  candidate=""
fi
pointer="$CACHE_ROOT/.current.$$"
ln -s "$verified" "$pointer"
mv -f "$pointer" "$current"
exec "$verified/bin/fkst-ops" --declaration "$declaration" "$@"
