# Checkout integrity and synchronization for deployment-operated source trees.

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

sync_to_run_branch() { # $1 worktree dir
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1 || { echo "  ! $1 is not a git worktree"; return 1; }
  git -C "$1" fetch origin "$INTEGRATION_BRANCH" -q 2>/dev/null \
    || { echo "  $1 -> FETCH-FAILED ($INTEGRATION_BRANCH)"; return 1; }
  local target; target=$(git -C "$1" rev-parse --short "origin/$INTEGRATION_BRANCH" 2>/dev/null) \
    || { echo "  $1 -> MISSING-REMOTE-BRANCH ($INTEGRATION_BRANCH)"; return 1; }
  # checkout -B (not reset --hard): leaves the checkout actually ON the integration branch
  # tracking origin/<integration>, instead of pointing a stale local 'dev' ref at integration content.
  local note checkout_status checkout_output
  checkout_output=$(mktemp "${TMPDIR:-/tmp}/fkst-checkout.XXXXXX") || return 1
  git -C "$1" checkout -q -B "$INTEGRATION_BRANCH" "origin/$INTEGRATION_BRANCH" 2>&1 \
    | tail -1 >"$checkout_output"
  checkout_status=${PIPESTATUS[0]}
  note=$(cat "$checkout_output")
  rm -f "$checkout_output"
  if [ "$checkout_status" -ne 0 ]; then
    echo "  $1 -> CHECKOUT-FAILED ($INTEGRATION_BRANCH)"
    return "$checkout_status"
  fi
  # Verify the checkout actually REACHED target, then self-heal. A checkout that aborts (working-tree
  # obstruction, a file<->symlink/dir transition racing the running supervise, a dirty tree) otherwise
  # leaves the clone on STALE code while the function returns ok and the supervise silently launches
  # stale - the exact "supervise silently re-running already-fixed defects" failure this tooling exists
  # to prevent. Self-heal forcefully (reset --hard + clean reaches the fetched ref regardless of the
  # obstruction; clean -fd keeps gitignored .fkst/ runtime), then re-assert the branch so the checkout
  # stays ON <integration>. If it STILL cannot reach target (deep corruption ensure_run_checkout should
  # have re-cloned), fail loud with STALE-CHECKOUT so the operator and doctor (pkg-stale) catch it.
  if [ "$(git -C "$1" rev-parse --short HEAD 2>/dev/null)" != "$target" ]; then
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

source_pin_values() { # $1 JSON resolved pin
  "$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["rev"])' "$1"
}

sync_to_pinned_revision() { # $1 worktree dir, $2 revision
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1 || {
    echo "  $1 -> PINNED-SOURCE-INVALID: not a git worktree"; return 1;
  }
  if ! git -C "$1" rev-parse --verify "$2^{commit}" >/dev/null 2>&1; then
    git -C "$1" fetch origin "$2" -q 2>/dev/null || {
      echo "  $1 -> PINNED-FETCH-FAILED ($2)"; return 1;
    }
  fi
  git -C "$1" checkout -q --detach "$2" 2>/dev/null || {
    echo "  $1 -> PINNED-CHECKOUT-FAILED ($2)"; return 1;
  }
  local head
  head=$(git -C "$1" rev-parse --verify HEAD^{commit} 2>/dev/null) || {
    echo "  $1 -> PINNED-REVISION-MISMATCH (missing HEAD; expected $2)"; return 1;
  }
  [ "$head" = "$2" ] || {
    echo "  $1 -> PINNED-REVISION-MISMATCH (got $head, expected $2)"; return 1;
  }
  echo "  $1 -> ${head:0:12} (pinned $2)"
}

sync_deployment_source() { # $1 worktree dir, $2 optional JSON resolved pin
  local pin="${2:-}" revision
  if [ -z "$pin" ] || [ "$pin" = "__FKST_OPS_EMPTY__" ]; then
    sync_to_run_branch "$1"
    return $?
  fi
  revision=$(source_pin_values "$pin") || {
    echo "  $1 -> PINNED-SOURCE-INVALID"; return 1;
  }
  sync_to_pinned_revision "$1" "$revision"
}

declared_package_source_checkouts() {
  "$PYTHON" -c 'import json,sys
for source in json.loads(sys.argv[1] or "[]"):
    print(source["root"],source["git"],sep="\t")' "${DECLARED_PACKAGE_SOURCES:-[]}"
}

ensure_declared_package_source_checkouts() {
  local checkouts source_root source_url
  checkouts=$(declared_package_source_checkouts) || {
    echo "  declared package sources -> INVALID"; return 1;
  }
  while IFS=$'\t' read -r source_root source_url; do
    [ -n "$source_root" ] || continue
    ensure_run_checkout "$source_root" "$source_url" || return 1
  done <<<"$checkouts"
}

sync_declared_package_sources() {
  local specs source_root source_pin
  specs=$("$PYTHON" -c 'import json,sys
for source in json.loads(sys.argv[1] or "[]"):
    resolved=source.get("resolved")
    pin=json.dumps(resolved,separators=(",",":")) if resolved is not None else ""
    print(source["root"],pin,sep="\t")' "${DECLARED_PACKAGE_SOURCES:-[]}") || {
    echo "  declared package sources -> INVALID"; return 1;
  }
  while IFS=$'\t' read -r source_root source_pin; do
    [ -n "$source_root" ] || continue
    sync_deployment_source "$source_root" "$source_pin" || return 1
  done <<<"$specs"
}

# Ensure a checkout's INTEGRATION_BRANCH is >= UPSTREAM_BRANCH by merging upstream
# FORWARD into integration and pushing. Why: operator out-of-band fixes land upstream; the
# deployment runs on integration; the in-pipeline sync_scan fast-forward can lag (or the running
# supervise is itself stale), so _proc_stale reads "current" against a stale integration and the
# supervise never picks up operator fixes. This deterministically merges upstream forward (plain
# fast-forward when integration is an ancestor; a merge commit when integration has its own
# un-rolled commits) and pushes, so the next _proc_stale sees pkg-stale and restarts onto the fix.
# Forward-only (never rewrites integration); conflicts remain for sync_conflict, and push failure
# is non-fatal.
ensure_integration_caught_up() { # $1 checkout dir
  local wt="$1"
  git -C "$wt" rev-parse --git-dir >/dev/null 2>&1 || return 0
  [ "$INTEGRATION_BRANCH" = "$UPSTREAM_BRANCH" ] && return 0
  git -C "$wt" fetch origin "$INTEGRATION_BRANCH" "$UPSTREAM_BRANCH" -q 2>/dev/null || return 0
  git -C "$wt" rev-parse --verify "origin/$INTEGRATION_BRANCH" >/dev/null 2>&1 || return 0
  git -C "$wt" rev-parse --verify "origin/$UPSTREAM_BRANCH" >/dev/null 2>&1 || return 0
  local behind; behind=$(git -C "$wt" rev-list --count "origin/$INTEGRATION_BRANCH..origin/$UPSTREAM_BRANCH" 2>/dev/null || echo 0)
  [ "${behind:-0}" -eq 0 ] && return 0
  echo "  $INTEGRATION_BRANCH is $behind behind $UPSTREAM_BRANCH in $(basename "$wt") -> merging $UPSTREAM_BRANCH forward"
  git -C "$wt" checkout -q -B "$INTEGRATION_BRANCH" "origin/$INTEGRATION_BRANCH" 2>/dev/null \
    || { echo "    WARN: could not checkout $INTEGRATION_BRANCH - leaving for sync_scan"; return 0; }
  if git -C "$wt" merge --no-edit "origin/$UPSTREAM_BRANCH" >/dev/null 2>&1; then
    if git -C "$wt" push origin "HEAD:$INTEGRATION_BRANCH" >/dev/null 2>&1; then
      echo "    merged + pushed: $INTEGRATION_BRANCH -> $(git -C "$wt" rev-parse --short HEAD)"
    else
      echo "    WARN: merge ok but push failed (perm/race) - leaving for sync_scan"
    fi
  else
    git -C "$wt" merge --abort 2>/dev/null
    echo "    WARN: $UPSTREAM_BRANCH does not merge cleanly into $INTEGRATION_BRANCH - leaving for sync_conflict"
  fi
}

# Emit one package per declared source because every package in one source carries its commit.
package_source_probes() {
  "$PYTHON" -c 'import json,sys
for entry in json.loads(sys.argv[1] or "[]"):
    if entry["packages"]:
        resolved=entry.get("resolved")
        revision=resolved.get("rev", "") if isinstance(resolved, dict) else ""
        print(entry["root"] + "\t" + entry["packages"][0] + "\t" + revision)' "${DECLARED_PACKAGE_SOURCES:-[]}"
}

package_source_config_status() {
  local specs source_root source_packages source_pin source_head desired
  specs=$("$PYTHON" -c 'import json,sys
for entry in json.loads(sys.argv[1] or "[]"):
    resolved=entry.get("resolved")
    revision=resolved.get("rev", "") if isinstance(resolved, dict) else ""
    print(entry["root"], ",".join(entry["packages"]), revision, sep="\t")' \
    "${DECLARED_PACKAGE_SOURCES:-[]}") || return 1
  while IFS=$'\t' read -r source_root source_packages source_pin; do
    [ -n "$source_root" ] || continue
    source_head=$(git -C "$source_root" rev-parse --verify HEAD^{commit} 2>/dev/null) \
      || source_head=unknown
    desired="${source_pin:-origin/$INTEGRATION_BRANCH}"
    printf 'root=%s packages=%s desired=%s checkout=%s\n' \
      "$source_root" "$source_packages" "$desired" "$source_head"
  done <<<"$specs"
}

# Parse only the last provenance field into an exact name -> version binding.
provenance_package_versions() { # $1 supervise log
  "$PYTHON" -c 'import sys
field = None
with open(sys.argv[1], encoding="utf-8", errors="replace") as stream:
    for line in stream:
        for token in line.split():
            if token.startswith("PKG_VERS="):
                field = token.removeprefix("PKG_VERS=")
bindings = {}
for item in (field or "").split(";"):
    name, separator, version = item.rpartition("@")
    if separator and name:
        bindings[name] = version
for name, version in bindings.items():
    print(name, version, sep="\t")' "$1"
}

provenance_package_version() { # $1 bindings, $2 exact package name
  local binding_name binding_version found=""
  while IFS=$'\t' read -r binding_name binding_version; do
    [ "$binding_name" = "$2" ] && found="$binding_version"
  done <<<"$1"
  printf '%s\n' "$found"
}

# A revision can be compared and can justify restart. Dirty and unknown are explicit producer
# states whose exact running revision cannot be established; restart cannot change either state,
# so report them diagnostically without returning an actuator verdict.
package_source_moved() { # $1 parsed provenance package bindings
  local probes probe_root probe_package probe_pin probe_running probe_head unverifiable=""
  probes=$(package_source_probes) || {
    echo "declared package-source probes are invalid; revision comparison skipped" >&2
    echo pkg-provenance-invalid
    return
  }
  while IFS=$'\t' read -r probe_root probe_package probe_pin; do
    [ -n "$probe_root" ] && [ -n "$probe_package" ] || continue
    probe_running=$(provenance_package_version "$1" "$probe_package")
    if [ -z "$probe_running" ]; then
      echo "package source $probe_root ($probe_package) has no provenance binding; revision comparison skipped" >&2
      echo pkg-provenance-invalid
      return
    fi
    if [ "$probe_running" = unknown ]; then
      echo "package source $probe_root ($probe_package) reported provenance unknown; revision comparison skipped" >&2
      [ "$unverifiable" = pkg-provenance-invalid ] \
        || unverifiable=pkg-provenance-unknown
      continue
    fi
    if [[ "$probe_running" =~ ^[a-f0-9]+-dirty$ ]]; then
      echo "package source $probe_root ($probe_package) reported provenance $probe_running; revision comparison skipped" >&2
      [ -n "$unverifiable" ] || unverifiable=pkg-provenance-dirty
      continue
    fi
    if ! [[ "$probe_running" =~ ^[a-f0-9]+$ ]]; then
      echo "package source $probe_root ($probe_package) reported invalid provenance $probe_running; revision comparison skipped" >&2
      unverifiable=pkg-provenance-invalid
      continue
    fi
    if [ -n "$probe_pin" ]; then
      probe_head="$probe_pin"
    else
      git -C "$probe_root" fetch origin "$INTEGRATION_BRANCH" -q 2>/dev/null \
        || {
          echo "package source $probe_root ($probe_package) could not fetch provenance comparison revision" >&2
          echo pkg-provenance-invalid
          return
        }
      probe_head=$(git -C "$probe_root" rev-parse --verify \
        "origin/$INTEGRATION_BRANCH^{commit}" 2>/dev/null) \
        || {
          echo "package source $probe_root ($probe_package) has no provenance comparison revision" >&2
          echo pkg-provenance-invalid
          return
        }
      if [ -z "$probe_head" ]; then
        echo "package source $probe_root ($probe_package) has an empty provenance comparison revision" >&2
        echo pkg-provenance-invalid
        return
      fi
    fi
    [ "${probe_head:0:${#probe_running}}" = "$probe_running" ] || { echo pkg-stale; return; }
  done <<<"$probes"
  [ -z "$unverifiable" ] || echo "$unverifiable"
}

package_source_launch_args() {
  "$PYTHON" -c 'import json,sys
for entry in json.loads(sys.argv[1] or "[]"):
    print("--package-source")
    print(entry["root"])
    print(" ".join(entry["packages"]))' "${DECLARED_PACKAGE_SOURCES:-[]}"
}
