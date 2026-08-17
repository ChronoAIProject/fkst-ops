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

`fkst-deployments` owns deployment parameters and composition: source bindings,
the mechanism pin, engine-revision derivations, package selection, integration policy, target identity, logical
runtime/durable/log identities, the complete cross-machine managed-bot roster,
provider selection, its bootstrap, and its lock. A machine profile supplies
discovered machine facts such as absolute paths, credentials, the local machine
actor (`bot_login`), and locally available binaries. Declarations refer to
machine facts by logical name.

The validator selects `bot_login` membership in the declaration-owned roster as
an fkst-ops invariant; this is not derived from platform behavior. Platform
consumers tolerate a peers-only list because they separately receive the local
actor, but fkst-ops treats the declared roster as the complete fleet and fails
closed when it omits that actor. The explicit cost is rejection of peers-only
rosters.

Artifact generation accepts exactly one explicit local actor (`--bot-login`)
per invocation and uses it for every declaration selected in that generation.
That actor must be a member of every operated deployment's declared
`managed_bot_logins` roster; generation fails closed if any roster omits it.
This is the one-machine/one-bot-app cardinality contract, not a per-deployment
multi-actor facility.

The platform has two distinct managed-bot classifiers, and fkst-ops does not
attempt to make them agree:

- Domain A is case-insensitive. `libraries/devloop/github_author_policy.lua:29`
  supplies `managed_bot_logins` and `:41` supplies `is_managed_bot_login`; both
  use the `devloop/base.lua:153` trim/lower/strip normalization. Its separate
  authorization path is `libraries/forge/github/content_filter.lua:370-380`
  through `content_filter.is_authorized:421` into
  `libraries/devloop/github_author_policy.is_authorized:87`. The consensus,
  liveness-scan, and loop departments consume that path.
- Domain B is case-sensitive. `packages/github-devloop-workflow/tools/workflow_board_fact.py:71-74`
  removes only the trailing `[bot]`, preserving case. The
  `libraries/forge/github/strings.lua:6` classifier is consumed by both
  `github-external-pr-intake` and `github-ratchet-migration-slicer`.

The mechanism aligns its comparison and membership behavior with Domain B. Its
additional fail-closed guarantee is narrower: each mechanism-approved
`managed_bot_logins` and `author_authorization.authorized_logins` list must not
self-collapse in the coarser Domain A (diagnostics identify both colliding
indices). This is a list-internal safety invariant only; it does not change
Domain B comparison or membership semantics. The local `bot_login` must retain
the existing Domain B membership rule, and is also rejected when it aliases a
different roster entry under Domain A.

Irreducible platform residual risk remains because the two managed-bot
classifiers can disagree, and an inbound author can be authorized after Domain
A case folding while not being recognized as a managed bot under Domain B. The
mechanism cannot close these gaps without changing platform consumers, which is
outside fkst-ops ownership. Operators must therefore treat authorization and
managed-bot facts as domain-specific: audit both normalized forms, expect the
same login to produce different department decisions, and expect an author
admitted by the Domain A policy to still be classified by Domain B consumers as
unmanaged or emitted as an external-intake candidate. Such disagreements
require investigation; an authorization result does not prove managed-bot
identity.

The two GitHub credential sources provide deliberately different evidence:

- `github-app` mints an installation token for the declared target and verifies
  that target against `/installation/repositories`. Its proof remains
  `target-access-only;bot-login-not-mechanically-proven`: it proves installation
  access to the target, but installation tokens do not resolve through `/user`,
  so it does not prove that the declared login is the token's principal.
- `github-cli-user` obtains the GitHub CLI's stored credential for the declared
  account. Live API reads compare that token's `/user` login exactly with the
  declared login and check that GitHub reports the declared target's
  `permissions.push` as exactly `true`; those facts may then be reused for at
  most 60 seconds, measured only by `CLOCK_MONOTONIC` within one boot session.
  The attestation records `kern.bootsessionuuid`; a changed boot session is a
  miss, and inability to establish either that identity or the monotonic clock
  disables attestation use. Runtime generation paths normally differ between
  launches, but path freshness is not a safety requirement: the explicit
  boot-session binding prevents attestation reuse across boots even when a
  generation path is retained or reused. The locally held token is retrieved
  on every invocation. If that retrieval
  observes a changed SHA-256 fingerprint, the attestation is invalidated on
  that invocation before the credential is emitted. Failed verification is
  never cached, and the launch-time authorization check always verifies live
  without reading or publishing attestation state. Concurrent misses wait at
  most 250 milliseconds for single-flight verification; a caller that cannot
  acquire the guard in that interval verifies independently and does not
  publish its result, so duplicate remote reads are permitted instead of
  extending the caller's lock wait. The live check performs no GitHub write and
  proves neither future access nor the effects of branch protection, rulesets,
  later revocation, or SSO changes. The credential is account-wide; the target
  check does not make it repository-scoped. Its proof is
  `login-verified;token-scope-account-wide-not-repository-scoped`.

The roster remains an operational assertion rather than an identity proof. It
is merged into Domain A's trusted-author allowlist by
`libraries/devloop/github_author_policy.lua:69-74`. Because the membership rule
structurally treats the roster as the complete-fleet union, `actor in roster`
can be satisfied by a peer's login. That invariant proves only that the
declaration did not omit this machine; it does not prove that the process is
running as this machine's own identity.

Every entry in either list is one non-empty platform token. A token must contain
no comma, no character Python recognizes as whitespace (including Unicode
whitespace), and no NUL. These exclusions preserve one-entry/one-token
semantics at the platform consumers and reject line terminators that Bash
command substitution can strip from its output; NUL is also excluded because
that substitution silently removes it. Domain B normalization removes one
trailing literal `[bot]`; the remaining case-sensitive identity must be
non-empty. The resolved machine `bot_login` and artifact generator's
`--bot-login` obey the same transport and non-empty-normalized-identity rules.
No broader GitHub-login grammar is implied.

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

## Engine revision authority

Each deployment declares a closed `engine_revision` relationship containing one
safe relative `path`. That path is always read from the platform checkout; there
is no configurable checkout tag or literal revision. The mechanism captures
commit `P` and reads revision `E` from the blob at `P:<path>`; it never reads the
mutable working-tree file. The verified authority reduction is narrow: the set
of writable records that can select which engine executes goes from three to
one, the revision file in the platform commit.

The declaration can select `engine_revision.path`, and `host_run.sh` accepts a
captured platform tree distinct from `--project-root`. For workspace packages,
both repositories must contain each other's `HEAD` commit; mutable origin URLs
do not establish this identity. These are expressible inputs, not authority
reductions.

Deployment-operated lock entries contain source identity and Git URL only. A
`resolved` table on such an entry is invalid. The mechanism entry is different:
its `resolved.rev` and `resolved.tree_sha256` remain required and are enforced by
exact `HEAD` and canonical tracked-tree equality before execution.

Target and platform checkouts are branch-operated. The engine checkout must be
a separate checkout and is detached at `E`. The engine provider accepts exactly
`engine_checkout`, `engine_binary`, `expected_revision`, `operation`, and
`build_command`; `expected_branch` and every other extra input are invalid. It
fetches `E` explicitly, checks out `E` detached, verifies `HEAD == E` before and
after the build, and returns `source_rev == E`. Checkout mutation and product
copying are serialized. The Cargo product is copied to the regular file
`<engine_binary>-E` with create-if-absent publication; existing content is never
replaced. Its v2 receipt binds `E`, the build command, and the published bytes'
SHA-256 digest, which is recomputed at reuse. A missing or mismatched receipt,
symlink, or conflicting existing artifact is not current. Only this
build-from-source case is supported today. The host consumer recomputes the
receipt digest again immediately before exec. This check detects incomplete or
corrupt publication; it is not tamper resistance. Tamper resistance is out of
scope because a same-user writer can also replace source checkouts,
declarations, the operator, and a matching receipt.

Launch captures `(P, E)`, selects `<engine_binary>-E`, and materialises a detached
platform checkout at `$RUNTIME_ROOT/.platform/P`. Reuse requires equal canonical
tree hashes for `P`, a clean materialised tracked tree, and the expected
derivation blob; an invalid snapshot is removed and rebuilt. `host_run.sh`
requires `E`, rejects a binary path naming another revision, and exports `E` to
the engine process. The foreground launcher opens and holds the platform and
engine revision lock descriptors before it forks; the executable child inherits
them through exec. This removes the spawn-to-lock interval with one descriptor
handoff, which is smaller than a readiness handshake and its protocol. Cleanup
takes the matching exclusive lock and removes only revisions selected by no
declaration and held by no live child; it has no age or count policy. A stable
guard serializes revision-lock creation and removal, so internal cleanup cannot
split lock identity. The guard path is never unlinked or recreated; external
maintenance must preserve that inode while operators or children may run.
Different deployments may select different `E` values from one binary stem
without conflict. A build-receipt failure or a later control-publication failure
can leave only an unselected revision-named artifact, which cleanup reclaims.

Artifact hydration compares every pre-existing target, platform, and engine
checkout's `origin` URL exactly with its declared lock source before fetching or
accepting it. A different origin fails with `CHECKOUT_SOURCE_MISMATCH`; matching
content at the requested SHA does not substitute for declared provenance.

## Cross-repository adoption order

The engine-derivation mechanism/schema change is adopted in this order:

1. Publish the mechanism revision first, while no deployment lock refers to it.
2. Then use one deployment commit to carry both the `engine_revision` and
   separate `engine_checkout` declaration changes and the new mechanism `rev`
   plus `tree_sha256`.
3. Run that mechanism revision's artifact generator. It derives every logical
   root, including the newly declared engine checkout, before hydration and
   profile validation, so no generated machine profile is hand-edited.

This ordering avoids a declaration/pin incompatibility in either repository
history because the entry self-pins from the `fkst.lock` in the same deployment
commit. An old deployment commit therefore uses the old mechanism that explains
its old declaration, while the new deployment commit atomically carries both
the new schema-shaped declaration and the mechanism pin that explains it. The
executable old/old, new/new, and crossed-pair matrix lives in
`tests/bootstrap/test_bootstrap.py`; the generated-engine-root evidence lives in
`tests/watch/test_generate_artifacts_publication.py`. This does not guarantee a
race-free window for concurrent cross-repository delivery, and it proves no
downstream engine or package adoption semantics; those remain explicitly
excluded below.

## Public action surface

The public deployment actions are exactly `board`, `status`, `logs`, `restart`,
`sync`, and `stop`. `doctor` is a separate operator entry, not a seventh public
deployment action. Internal commands and functions are not public API.

| Entry | Mutates | Validation or checks before mutation |
|---|---|---|
| `board` | No deployment lifecycle state. Providers may perform remote reads. | Common entry validation, then provider contract and result-shape checks. |
| `status` | No. | Common entry validation; identifies the declared supervisor before reporting process and provenance state. |
| `logs` | No. | Common entry validation; resolves the declared log identity before selecting and tailing its latest log. |
| `restart` | Yes: source checkouts, supervisor process, runtime generation, logs, and possibly engine artifacts. It may block on a full engine build and fail before process replacement if that build fails. | Common entry validation; branch checkout sync; packages-derived engine pair; revision-addressed byte receipt; clean captured platform tree; child revision binding; numeric durable PID-file parsing and liveness checks before replacement. |
| `sync` | Yes: declared run branches/checkouts and engine artifacts; restarts only when loaded package or engine code is stale. | Common entry validation; provider and checkout identity; per-deployment forward integration; packages-derived engine pair; revision-addressed byte receipt; and running-provenance checks before the corresponding mutation. |
| `stop` | Yes: sends `SIGKILL` to one PID. | Common entry validation; resolves the declared deployment and reads its durable PID file. `stop all` attempts every selected deployment and returns nonzero if any attempt fails. |
| `doctor` | Conditionally: guarded leaked-test reaping and stale-receipt cleanup. | Common entry validation; each repair independently checks process identity, parent/orphan and age guards, or receipt identity and age. Findings and failures remain visible. |

`preflight` runs the common entry validation without dispatching an action. It
validates credential configuration only; it does not authenticate, mint a
credential, or perform either source's refresh checks.

## Selected process topology

The supervisor is launched directly as a new session and process-group leader
using `os.setsid()` followed by in-place `exec`. Readiness is accepted only
after the process stays alive and emits the startup readiness evidence; the
operator then checks and reports whether `PGID == PID`.

Before that exec, the launch loader must set the supervisor's soft open-file
limit to the host kernel's `kern.maxfilesperproc` value and verify the value from
the process's own resource-limit state. The requirement cannot be expressed as
a smaller application constant: consensus runs five seats, but each seat starts
a general Codex process whose descriptor use is neither bounded nor owned by
this repository. Thus `5 * unbounded seat use + framework use + headroom` has no
finite source-derived sum, and the required limit is the kernel's declared
per-process maximum. Failure to read, set, or verify that value must stop the
launch before the supervisor starts. Consensus is load-bearing, so reporting a
deployment healthy when its required process resources are unavailable would be
a false healthy state.

Artifact generation discovers required mechanism executables before publication.
`codex` is required because a supervisor that cannot spawn it cannot execute its
departments. The engine has no executable override for this consumer, so the
supervisor child receives a deterministic `PATH`: the mechanism entry directory,
the directory of the interpreter that resolved the operator, the directories of
every tool in the generated machine profile, and the platform default executable
path, with duplicates removed. The generator's or operator's ambient `PATH` is
not inherited by the child.

For a self-hosted target, the mutable project checkout and captured platform
checkout have different paths. The host-run contract accepts workspace platform
packages from that captured root only when each Git object database contains the
other checkout's `HEAD`; an unrelated root with a copied origin URL fails closed.

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

The `credential.github` configuration is closed to `source`. Its concrete
source set is exactly `github-app` and `github-cli-user`. A declaration may use
one of those literals or a `machine:<logical-name>` reference; validation
resolves such a reference through the machine profile's logical defaults before
checking the closed set and stores only the concrete value. The same resolved
value propagates to both the authorization check and the supervised process, so
launch authorization and later refreshes cannot announce different sources.

`provider.implementation` has the form
`<source-id>:<safe-relative-entry>`. The source must be one of the
deployment's bound target, platform, or engine sources, or its declared
mechanism source. Absolute entries, traversal, checkout escape, missing source bindings,
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
- a race-free window for concurrent cross-repository delivery of a declaration
  and its mechanism pin; the tested old/new compatibility matrix proves only
  that matching pairs pass and crossed pairs fail closed at the real pinned
  re-exec boundary;
- downstream engine or package adoption semantics inferred from that pin/re-exec
  compatibility matrix;
- conforming replacement when two replacement invocations overlap in the
  current implementation, or atomic serialization of cache publication,
  old-generation pruning, or rollback;
- equivalence with a legacy operator unless the deployment-owned acceptance
  matrix has actually been run and recorded; or
- successful mutation when a checkout is dirty/diverged, a provider fails, a
  pin or tree does not verify, readiness is absent, or required machine facts
  are unavailable.
- for `github-app`, that an explicitly declared `bot_login` is mechanically
  proven to be the identity used by the credential provider; or for either
  source, that roster membership proves the local process is not using a peer's
  login.

The executable behavior remains the final evidence for implementation defects;
changes to the guarantees above require changing this specification and the
tests that prove the affected contract.

⟦AI:FKST⟧
