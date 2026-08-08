# Cadence Entry Design

The repository-owned `watch/cadence_round.py` defines one round and owns no scheduler. It discovers
every TOML document with schema `fkst.ops.deployment.v1` below an explicitly supplied deployment
repository, invokes the repository's `bin/fkst-ops` entry with `sync` and then `status`, and appends
one JSON Lines record per declaration. A failed declaration does not stop later declarations and
makes the round fail.

The round accepts the deployment repository, machine profile, ledger, and operator entry only from
arguments or the corresponding `FKST_WATCH_*` environment variables. It passes its environment
through unchanged and never assigns `FKST_GITHUB_WRITE`, so write posture remains machine-owned.
Scheduling policy is deployment-owned. Every declaration carries the one positive integer
`cadence_interval_seconds`; all declarations in one deployment repository must agree. Machine
paths are discovered. `bin/fkst-regenerate <deployment-repository>` combines both with the
committed launchd template and writes the generated LaunchAgent. There is no operator-selected
plist literal.

The same command generates `.fkst/machine-profile.toml`. Logical root and binary names map under
the conventional `$HOME/.fkst/machine` base, the bot login comes from `gh api user`, managed bot
sets come from each declaration's `managed_bot_logins`, and integration branches remain explicit
declaration parameters. It invokes the unchanged real validator for every declaration before it
writes the LaunchAgent.

The deployment repository path is the only genuinely required input: discovery cannot know which
of potentially many deployment repositories the operator intends to regenerate. The derived files
are the deployment repository's `.fkst/machine-profile.toml` and
`$HOME/Library/LaunchAgents/com.fkst.cadence.plist`, both produced by
`watch/generate_artifacts.py`. The ledger and standard streams are runtime records created later
by the scheduled round and launchd, not hand-authored inputs.
