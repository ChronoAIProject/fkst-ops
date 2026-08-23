# Host-layer migration

## Decision

The host-launch layer moves into this repository. The platform repository keeps its remaining
copies until they are shown to have no consumers. Nothing further is deleted before that.

The layer here is `host_run.sh`, `host_entry.sh`, `bin_bootstrap.sh`, `bin_cache.py`,
`run_bin.sh`, `warm_pinned_bin.sh` and `supervise.sh`.

## Why the layer belongs here

Every `host_run_*` call site in the platform's `scripts/run.sh` was inside `cmd_supervise` or
`cmd_supervise_old`. Its `test`, `test-affected`, `test-composed` and `run` subcommands called
none of them, so the layer served `supervise` alone — as `host_run.sh`'s own first line stated.
`supervise` was invoked by the operator, and the operator was this repository.

Living in the platform repository was what gave that repository a status no other package
repository had: every deployment on a machine took its platform from a checkout of it, so a
repository that developed the engine had to borrow one in order to run its own devloop.

## Removal order

The initial extraction copied the layer here on 2026-08-08 and did not remove the originals.
Both sides were then edited independently for ten days. On 2026-08-17 a change added an
`--expected-engine-revision` option to the copy here; it passed this repository's suite and six
review seats, and then broke `restart` and `sync` on a live machine, because the executed file
was the platform's and it rejected the option. Two further differences had to be carried to the
executed file one production failure at a time.

The lesson is not that these copies are wrong. It is that **two live copies are wrong**.
Removing the ones here would have made the platform repository the permanent owner, which was the
opposite of the decision above. Removing the platform's before this repository could launch would
have left no working launcher at all. So the order is: build here, cut over, and only then remove
there — when absence of consumers is demonstrated rather than assumed.

## Sequence

1. Bring the platform implementations forward into `host/`, so this repository holds the
   complete layer rather than a partial snapshot. This step has landed: `host/` now holds
   `host_run.sh`, `host_entry.sh`, `bin_bootstrap.sh`, `bin_cache.py`, `run_bin.sh` and
   `warm_pinned_bin.sh`.
2. Move the `supervise` entry point here. This step has landed: `ops/deployment_operator.sh` now
   launches `$_repo_root/host/supervise.sh`.
3. Cut over one deployment, verify it launches, then the other.
4. Demonstrate that the platform's remaining copies have no consumers, then remove them there.
   The supervise half has landed:
   `scripts/host_run.sh`, `cmd_supervise` and `cmd_supervise_old` are gone, while
   `scripts/host_entry.sh`, `scripts/bin_bootstrap.sh`, `scripts/bin_cache.py`,
   `scripts/run_bin.sh` and `scripts/warm_pinned_bin.sh` remain.

The landed half of step 4 removed the platform's `run.sh supervise <package>` developer entry
point. That entry point was the special status; its removal was part of the goal rather than a
cost of it.

## Consequences to settle during the migration

- The platform's `scripts/run.sh` must keep working with only a platform checkout present,
  because every implementation attempt runs `test-affected` in a bare worktree. Nothing in the
  migration may make its `test`, `test-affected`, `test-composed` or `run` subcommands depend on
  this repository.
- The platform's `scripts/host_run.sh` and its `supervise` commands are gone. Any control-plane
  composition document that still describes the HOST-RUN contract as owned there is already
  stale and is the platform repository's to update.

⟦AI:FKST⟧
