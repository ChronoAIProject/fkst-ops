# Host-layer migration

## Decision

The host-launch layer moves into this repository. The platform repository keeps its copies
until this repository can launch a supervise on its own and its copies are shown to have no
consumers. Nothing is deleted before that.

The layer is `host_run.sh`, `host_entry.sh`, `bin_bootstrap.sh` and `bin_cache.py`, together
with the `supervise` entry point that reaches them.

## Why the layer belongs here

Every `host_run_*` call site in the platform's `scripts/run.sh` is inside `cmd_supervise` or
`cmd_supervise_old`. Its `test`, `test-affected`, `test-composed` and `run` subcommands call
none of them, so the layer serves `supervise` alone — as `host_run.sh`'s own first line states.
`supervise` is invoked by the operator, and the operator is this repository.

Living in the platform repository is what gives that repository a status no other package
repository has: every deployment on a machine takes its platform from a checkout of it, so a
repository that develops the engine must borrow one in order to run its own devloop.

## Why nothing is deleted yet

The initial extraction copied the layer here on 2026-08-08 and did not remove the originals.
Both sides were then edited independently for ten days. On 2026-08-17 a change added an
`--expected-engine-revision` option to the copy here; it passed this repository's suite and six
review seats, and then broke `restart` and `sync` on a live machine, because the executed file
is the platform's and it rejected the option. Two further differences had to be carried to the
executed file one production failure at a time.

The lesson is not that these copies are wrong. It is that **two live copies are wrong**.
Removing the ones here would make the platform repository the permanent owner, which is the
opposite of the decision above. Removing the platform's before this repository can launch would
leave no working launcher at all. So the order is: build here, cut over, and only then remove
there — when absence of consumers is demonstrated rather than assumed.

## Measured state at the time of the decision

Divergence between the two copies:

| file | here | platform | differing lines |
|---|---|---|---|
| `host_run.sh` | 790 | 728 | 86 |
| `host_entry.sh` | 708 | 642 | 76 |
| `bin_bootstrap.sh` | 72 | 240 | 258 |
| `bin_cache.py` | 73 | 73 | 4 |

The copies here are a partial snapshot rather than a mirror: `bin_bootstrap.sh` is missing 168
lines of the platform implementation, so they cannot simply be made live. The complete
implementations are the platform's, and the migration carries those forward.

Nothing in this repository executes the copies here. They are referenced by `tests/host/` and,
since the per-target engine-binary fix, by a docstring in `doctor/targets.py` noting that it
derives the engine binary through the same `ops.revision_derivation` module that
`host/bin_bootstrap.sh` uses. That last one is prose: `doctor/targets.py` imports the module
directly and does not run the shell copy.

## Sequence

1. Bring the platform implementations forward into `host/`, so this repository holds the
   complete layer rather than a partial snapshot.
2. Move the `supervise` entry point here. `ops/deployment_operator.sh` currently requires
   `scripts/run.sh` inside the launch platform; it launches this repository's own entry instead.
3. Cut over one deployment, verify it launches, then the other.
4. Demonstrate that the platform's copies have no consumers, then remove them there together
   with `cmd_supervise` and `cmd_supervise_old`.

Step 4 removes the platform's `run.sh supervise <package>` developer entry point. That entry
point is the special status; its removal is part of the goal rather than a cost of it.

## Consequences to settle during the migration

- The platform's `scripts/run.sh` must keep working with only a platform checkout present,
  because every implementation attempt runs `test-affected` in a bare worktree. Nothing in the
  migration may make its `test`, `test-affected`, `test-composed` or `run` subcommands depend on
  this repository.
- The platform's control-plane composition document describes the HOST-RUN contract as owned by
  its own `scripts/host_run.sh`. That description is accurate today and becomes wrong at step 4;
  it is updated then, not before.

⟦AI:FKST⟧
