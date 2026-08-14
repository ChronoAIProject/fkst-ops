---
name: operate-fkst-deployment
description: Use when bringing up, diagnosing, or verifying an FKST deployment appliance on a machine — install the prerequisites, author the machine profile, preflight, start, and prove the appliance is actually working rather than merely started. Covers the failure modes where `status` reports RUNNING and HEALTHY while a whole layer is dead.
---

# Operate an FKST deployment

`fkst-ops` is the pinned mechanism. `fkst-deployments` is machine-independent
configuration. A machine joins the fleet by supplying one ignored machine profile
and nothing else — no fork of the declarations, no local edits.

This skill is the operating doctrine for that. It is not a wrapper: the entrypoint
is `bin/fkst-ops` and stays the only invocation surface.

## Read first

- `README.md` and `SPEC.md` in this repository — the mechanism's own contract.
- `fkst-deployments/README.md` — the configuration boundary and machine setup.
- The declarations you will operate. Every logical name in your profile is derived
  from them; do not copy a profile from another machine and edit it.

## Non-negotiable guardrails

- **`RUNNING` and `health=HEALTHY` do not mean the appliance is working.** They
  report that the supervise process is alive. A whole layer can be dead beneath
  them. The named anti-example: an appliance ran for three hours reporting
  `health=HEALTHY auth-fail=0 panic=0` while `github-devloop-integration.rollup_scan`
  and `.sync_scan` failed on every single round, because its integration branch did
  not exist on the remote. Nothing in the status line said so. Verify by the
  procedure below, never by the status line alone.
- **Never hand-edit the machine profile of a running deployment without a
  preflight.** `preflight` validates the whole configuration and dispatches
  nothing; it is free. Run it after every profile change.
- **Never run artifact generation with a mechanism that does not match the lock
  pin.** It refuses, and the refusal is correct — it is the same self-pinning
  discipline the entrypoint enforces. Advance the pin instead.
- **A declaration reshape and its pin advance travel in ONE commit.** Either half
  alone leaves the configuration repository unusable: the new field is required by
  the new mechanism and meaningless to the old one. The self-pinning entrypoint is
  what makes this safe — every commit carries the mechanism that interprets its own
  declarations, so no intermediate state exists. The named anti-example: a PR that
  introduced `integration_branch = "machine:<logical>"` alone passed validation and
  made the repository impossible to install, because that reference had no
  generator-side implementation at the pinned revision.
- **Follow the authoritative policy in `fkst-deployments`.** This repository does
  not restate that policy; consult `fkst-deployments/README.md`.
- **Do not add a second scheduler.** launchd `StartInterval` plus the cadence round
  is the fleet's own timing, and the cadence guard is its own recovery. A
  session-bound loop is not a monitoring mechanism — see "Where /loop belongs".

## Bring-up, in order

Each step below has failed in practice; the ordering is the cheap-to-verify one.

1. **Engine binary.** `cargo build -p fkst-framework` in the `fkst-substrate`
   checkout. This is the longest pole and the validator requires the binary to
   exist and be executable, so do it first. A machine with no Rust toolchain needs
   one installed before anything else can be validated.
2. **Python.** The mechanism needs `tomllib`, so Python 3.11 or newer. A system
   `python3` may be older; point `FKST_OPS_PYTHON` at a suitable interpreter rather
   than changing the system one.
3. **Source checkouts** at the revisions the lock pins — target, platform, and
   engine, per the declaration's `[deployment.machine]` logical names.
4. **Machine profile.** Derive every logical name from the declarations rather than
   from an example file; examples drift. `[roots]`, `[binaries]`, and `[tools]`
   paths must be absolute.
   - `[tools]` must carry every executable the mechanism itself needs, not only the
     ones named by provider commands. `codex` in particular accepts **no**
     environment override and can only come from the profile. `gh` and `gh-app`
     accept environment overrides, but put them in the profile anyway: an
     unattended launch inherits no operator shell.
   - Resolve `gh` to the real binary. A shell function of the same name will
     shadow it and `command -v` will return the function.
5. **Integration branch.** It must already exist on every operated remote. Create
   it at that repository's `dev` tip, matching the fleet's convention. **The
   appliance does not create it**, and without it the integration departments fail
   on every round while the status line still reports healthy.
6. **`preflight`** for each declaration. Silence and exit 0 is success.
7. **Start**, then verify — the next section.

## Proving the appliance actually works

Do all four. Any one alone has been misleading in practice.

- **Department activity.** Read the supervise log and count what ran. A working
  appliance shows a broad distribution — issue and PR observation, intake
  admission, liveness scan, GitHub polling, workflow materialization, and the
  integration departments. A narrow distribution means a layer is missing.
- **Exit-code distribution.** `exit=75` is `EX_TEMPFAIL`, the soft-failure
  convention, and a few are normal backoff. A department at or near 100 percent
  `exit=75` is not backing off; it is structurally blocked. That is exactly how the
  missing-integration-branch failure presents.
- **The ledger.** Each cadence round appends one record per deployment. It carries
  the status line and, when the guard acted, a `guard` key.
- **The remote.** A working appliance advances its integration branch. If the
  branch has not moved from `dev` tip after the appliance has been up for a while,
  the integration half is not running regardless of what the status line says.

## The cadence guard

The cadence round runs `sync`, then `status`, then appends its record, and only
then may recover. `guard_restart_attempt_limit` is declared policy: a non-negative
integer, required, no default held by the mechanism, and zero disables the guard
entirely. With a zero limit, it writes one record per declaration without
`deployment_id` or `guard`, and never calls `restart`; with a positive limit, it
writes one record per deployment identity.

Two properties matter when reading its behaviour:

- **A restart that exits zero proves nothing.** Only the next round's observation
  does. The breaker counts consecutive `STOPPED` observations, so a restart that is
  accepted and then dies again still consumes budget.
- **A tripped breaker stays tripped.** The deployment stays honestly `STOPPED` and
  every subsequent round records `guard=open`. There is no half-open state and no
  automatic reclose, because a guard that silently retries forever converts an
  unsolved defect into an invisible one. Any `RUNNING` observation — including one
  produced by a person restarting by hand — breaks the streak and refills the
  budget.

The budget is not free. Every attempt takes the `restart` path, which fetches and
may rebuild the engine, against a GitHub quota shared with everything else on that
machine. Fleet-wide the guard is bounded by `machines x deployments x limit`;
choose the limit so that product stays acceptable during an outage where quota is
already scarce, and derive it rather than picking it.

## Where /loop belongs

`/loop` is for an operator session investigating something — a periodic
`反思自检` over a live appliance finds real problems. It is **not** the fleet's
monitoring mechanism, for one decisive reason: anything tethered to a session dies
with that session. That is the failure this fleet already had, and the cadence
LaunchAgent plus the guard is what fixed it. Adding a session-bound monitor would
reintroduce the coupling and give the fleet two schedulers whose failures look
different.

Use `/loop` to investigate. Let launchd and the cadence guard do the monitoring.

Mechanism-claim evidence index: [SPEC.md](SPEC.md).
