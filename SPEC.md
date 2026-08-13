# fkst-ops Specification

This file is the sole normative owner of guarantees made by `fkst-ops`. Other
repository documents are orientation, contributor instructions, or historical
records and must point here instead of restating current contracts.

## Scope and ownership

`fkst-ops` owns generic operational mechanism: its entry, declaration and
provider validation, lifecycle actions, observation, and generic board
orchestration. It is parameterized and does not own a concrete deployment.
Repository Python code uses only the Python standard library. Changing that
dependency constraint requires a deliberate revision of this specification.

`fkst-deployments` owns deployment parameters and composition: source bindings
and pins, package selection, integration policy, target identity, logical
runtime/durable/log identities, provider selection, its bootstrap, and its
lock. A machine profile supplies discovered machine facts such as absolute
paths, credentials, authenticated identity, and locally available binaries.
Declarations refer to machine facts by logical name.

Package producers own the meaning of their facts. Engine behavior and
guarantees belong to the engine repository. This repository consumes the
engine through its declared provider contract and refers to the engine's own
guarantee matrix; it does not reproduce that matrix or strengthen its claims.

## Entry and validation

An invocation supplies a deployment declaration, machine profile, and lock.
Before dispatch, the entry:

1. reads the deployment-owned mechanism pin;
2. verifies that the executing or cached checkout's `HEAD` is the pinned full
   revision and that the blobs of that named commit have the pinned canonical
   tracked-tree hash, hydrating a candidate when necessary;
3. re-executes the physically pinned entry with bounded recursion; and
4. validates the declaration, machine references, lock bindings, provider
   kinds and contracts, checkout roots, package roots, and provider entries.

Validation failure exits nonzero before the selected operational action runs.
Candidate acquisition may create and remove private cache staging paths. It
does not mutate deployment runtime, durable state, or resolved source working
checkouts. Cache publication and pointer replacement occur only after candidate
verification and pinned preflight succeed.

This verification does not inspect the checkout's current tracked-file
contents. Modified or deleted tracked working-tree files can therefore pass
when `HEAD` and the named commit tree match the lock. Verification of the
actual executable contents is a required invariant that is not implemented.

## Public action surface

The public deployment actions are exactly `board`, `status`, `logs`, `restart`,
`sync`, and `stop`. `doctor` is a separate operator entry, not a seventh public
deployment action. Internal commands and functions are not public API.

| Entry | Mutates | Validation or checks before mutation |
|---|---|---|
| `board` | No deployment lifecycle state. Providers may perform remote reads. | Common entry validation, then provider contract and result-shape checks. |
| `status` | No. | Common entry validation; identifies the declared supervisor before reporting process and provenance state. |
| `logs` | No. | Common entry validation; resolves the declared log identity before selecting and tailing its latest log. |
| `restart` | Yes: source checkouts, supervisor process, runtime generation, logs, and possibly engine artifacts. | Common entry validation; engine availability; declared checkout identity and cleanliness/divergence checks before sync; numeric durable PID-file parsing and liveness checks before replacement. |
| `sync` | Yes: declared run branches/checkouts and engine artifacts; restarts only when loaded package or engine code is stale. | Common entry validation; provider and checkout identity; forward-integration, dirty/diverged, build, and running-provenance checks before the corresponding mutation. |
| `stop` | Yes: sends `SIGKILL` to one PID. | Common entry validation; resolves the declared deployment and reads its durable PID file. `stop all` attempts every selected deployment and returns nonzero if any attempt fails. |
| `doctor` | Conditionally: guarded leaked-test reaping and stale-receipt cleanup. | Common entry validation; each repair independently checks process identity, parent/orphan and age guards, or receipt identity and age. Findings and failures remain visible. |

`preflight` runs the common entry validation without dispatching an action.

## Selected process topology

The supervisor is launched directly as a new session and process-group leader
using `os.setsid()` followed by in-place `exec`. Readiness is accepted only
after the process stays alive and emits the startup readiness evidence; the
operator then checks and reports whether `PGID == PID`.

Artifact generation discovers required mechanism executables before publication.
`codex` is required because a supervisor that cannot spawn it cannot execute its
departments. The engine has no executable override for this consumer, so the
supervisor child receives a deterministic `PATH`: the mechanism entry directory,
the directory of the interpreter that resolved the operator, the directories of
every tool in the generated machine profile, and the platform default executable
path, with duplicates removed. The generator's or operator's ambient `PATH` is
not inherited by the child.

This direct topology is deliberate policy. A separate shipped process root
would add no process-group isolation because the supervisor already owns its
session and process group. More importantly, replacement sends `SIGKILL` only
to the numeric PID read from the durable PID file rather than cascading through
its process group. The mechanism does not signal descendants or their process
group. Continued descendant lifetime and cross-generation adoption are engine
behavior, not a repository guarantee; the intent is to permit the engine to
preserve and adopt in-flight work. Output still bound to an old owner or runtime
is not guaranteed to reach the new runtime.

The historical recurring out-of-band `SIGTERM` is an unproven inference. A
prior supervisor was observed in its launcher's process group, but the alleged
group-directed signal was never observed live. Session isolation closes the
observed exposure; this specification does not claim that it explains or
eliminates the recurrence.

## Replacement state machine

A conforming replacement requires the following state machine. The current
implementation does not yet conform to its `Claim`, identity portions of
`Resolve` and `Retire`, or adoption portions of `Complete`; those items are
required invariants, not descriptions of current behavior.

1. **Claim:** acquire the deployment-scoped replacement serialization guard.
   Failure to acquire it fails without entering replacement.
2. **Resolve:** validate the declaration and resolve the current PID from the
   deployment-owned durable identity. Reject malformed, stale, or mismatched
   identity evidence. Rejection of mismatched or reused live PID evidence is
   not implemented.
3. **Retire:** verify the existing supervisor's command and session identity,
   signal only that intended PID with `SIGKILL`, then confirm that PID exits.
   Command and session identity verification are not implemented. Descendants
   and their process group are not signaled by the mechanism.
4. **Start:** create a fresh runtime generation while reusing the declared
   durable state, then launch the supervisor under the selected direct
   topology.
5. **Prove ready:** require both process survival and startup readiness within
   the bounded wait, establish and check the new session identity (`PGID ==
   PID`), and report the session check and log identity.
6. **Complete:** publish success, permit the engine to preserve registered work
   owned by any surviving descendants from older generations, and release the
   serialization guard. Preservation and adoption are engine invariants and
   are not guaranteed here.

Today, restart accepts a numeric durable PID file, checks whether that PID is
live, sends `SIGKILL` to it without command or session identity matching, and
waits for it to exit. A stale PID file whose PID has been reused can therefore
terminate an unrelated process. Malformed PID files fail closed, and failure to
signal or observe exit prevents launch of a second supervisor. If launch or
readiness fails, the command exits nonzero and reports the relevant log tail.
The old supervisor is
not resurrected and rollback is not guaranteed; the deployment may therefore
be left without a ready supervisor. A narrowly identified transient durable
lock-release race is retried a bounded number of times; other failures fail
immediately.

The current host-run implementation has PID-file exclusion but does not
serialize the whole replacement transaction. It therefore does not yet satisfy
the `Claim` invariant above under concurrent replacement. This is an explicit
adoption blocker, not a guarantee inferred from the PID file. Publication,
old-generation pruning, and rollback likewise require serialization work before
an adoption pin advances.

## Provider binding and transport

Providers are direct executable ports, not a general plugin framework. Each
deployment binds exactly one provider of each required kind:
`credential.github`, `engine`, `board.engine-durable`, and
`board.github-control`. Each binding selects the closed contract for its kind.

`provider.implementation` has the form
`<pinned-source-id>:<safe-relative-entry>`. The source must be one of the
deployment's bound target, platform, or engine sources, or its declared
mechanism source. Absolute entries, traversal, checkout escape, missing pins,
unbound sources, and missing or non-executable entries fail validation.

Mechanism-owned entries are bindable only when their path and kind appear in
`schema/provider_surface.py`; executable file presence alone does not publish a
provider. The closed published surface is:

| Path | Kind |
|---|---|
| `providers/github_credential_gh.py` | `credential.github` |
| `providers/engine.py` | `engine` |
| `providers/board_engine_durable.py` | `board.engine-durable` |
| `providers/board_github_control.sh` | `board.github-control` |

The engine and two board ports use the common `fkst.ops.invocation.v1`
transport. Input is one UTF-8 JSON document on stdin with exactly
`{"version":"fkst.ops.invocation.v1","contract":"<ContractId>","input":{}}`.
Output is one UTF-8 JSON document on stdout with exactly either the success
envelope `{"version":"fkst.ops.invocation.v1","ok":true,"result":{}}` or the
failure envelope
`{"version":"fkst.ops.invocation.v1","ok":false,"failure":{"code":"<FailureCode>","message":"<String>","details":{}}}`.
The object members carried by `input`, `result`, and `details` are defined by
the selected contract. Stderr is diagnostic only and is not result data.

A success object requires exit `0`; a failure object requires exit `1` or `2`.
Malformed or missing JSON, multiple JSON documents, unknown envelope members,
an exit/object mismatch, an unknown contract or failure code, or a result that
violates the selected contract fails closed as a provider port failure. The
credential provider uses its separately validated
`fkst.ops.credential.github.v1` command contract and is not passed through the
common invocation envelope.

## What is not guaranteed

This repository does not guarantee:

- engine semantics, engine delivery behavior, or engine adoption correctness
  beyond the engine repository's own guarantee matrix;
- the correctness, availability, or meaning of package-produced or remote
  provider facts;
- graceful shutdown, descendant termination, delivery of old-owner output to a
  new runtime, zero downtime, or automatic rollback after replacement failure;
- that a successful `setsid` explains or eliminates the unobserved recurring
  `SIGTERM`;
- scheduling, launchd installation, deployment discovery, concrete deployment
  declarations, machine paths, credentials, pins, or pin advancement;
- conforming replacement when two replacement invocations overlap in the
  current implementation, or atomic serialization of cache publication,
  old-generation pruning, or rollback;
- equivalence with a legacy operator unless the deployment-owned acceptance
  matrix has actually been run and recorded; or
- successful mutation when a checkout is dirty/diverged, a provider fails, a
  pin or tree does not verify, readiness is absent, or required machine facts
  are unavailable.

The executable behavior remains the final evidence for implementation defects;
changes to the guarantees above require changing this specification and the
tests that prove the affected contract.

⟦AI:FKST⟧
