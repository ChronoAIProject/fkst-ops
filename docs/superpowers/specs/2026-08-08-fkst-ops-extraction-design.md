# fkst-ops Extraction Design

**Status:** Approved implementation specification  
**Date:** 2026-08-08  
**First adopter:** `packages`

## 1. Purpose and Success Contract

`fkst-ops` is the version-controlled, repository-agnostic operational mechanism for FKST deployments. A deployment repository pins `fkst-ops`, supplies a declaration, and invokes its self-pinning entry with deployment-owned configuration. The dependency direction is strictly `deployment repository -> pinned fkst-ops`.

`fkst-ops` never depends on, names, or contains a declaration for a concrete repository. Adding deployment N+1 requires one declaration and one content pin, with zero source changes in `fkst-ops`.

The six actions `board`, `status`, `logs`, `restart`, `sync`, and `stop` are the public operator surface. `stop` sends SIGKILL to the supervise process; deployments are crash-only and recover from durable state, so it is not a graceful shutdown. The implementing operator layer owns this public action declaration, and the self-pinning entry derives its usage and routing from that declaration. The current operator dispatches status at `.claude/skills/dogfood-github-devloop/dogfood.sh:829`; `status` remains observational and performs no mutation. `doctor` remains a separately invocable operator entry in `fkst-ops`, preserving unchanged the current behaviour dispatched at `.claude/skills/dogfood-github-devloop/dogfood.sh:830` and implemented at `:710-720`. It retains durable health inspection, stray supervise detection, guarded leaked-test reaping, and stale receipt cleanup, whose current functions begin at `:567,602,633,696`. The mutating implementations kill guarded process groups at `:675-679` and remove stale receipts at `:696-705`. When `doctor` runs is owned outside `fkst-ops`; `fkst-ops` defines no trigger field, scheduler, or invocation event. `doctor` has fail-visible accounting and acceptance coverage separate from public-action equivalence. Private primitives required by the public actions and `doctor` remain internal; `bin`, `start`, and `config` are not routable through the public entry.

## 2. Owner Acceptance Record

The `worth` seat approved this migration conditionally. The decisive assumption is that at least one real deployment adopts `fkst-ops`, is verified equivalent, and eventually triggers deletion of the old operational entry. The repository owner accepts this lifecycle cost with the amendment that deletion is not urgent.

- **Accepted debt:** old operational entries remain in place after cutover.
- **Containment boundary:** after a deployment cuts over, its old entry is **READ-ONLY FROZEN**. Every operational-behaviour change is made in `fkst-ops` only. Editing both sides is forbidden.
- **Removal condition:** either event triggers deletion: (a) the deployment has run on `fkst-ops` for a soak window with the producer-declared public-action equivalence acceptance passing; or (b) any behavioural divergence is observed between the two entries.
- **Named first adopter:** the `packages` deployment.
- **Normative constraint:** "temporary without a removal condition" is not acceptable. The removal condition is normative, not advisory.

The owner records a finite soak duration in the `packages` cutover record before cutover.

## 3. Four Ownership Layers

### 3.1 L-mechanism: fkst-ops

`fkst-ops` owns the generic executor, deployment-declaration schema and all resolved-schema and provider-contract validation, lifecycle execution and observation reachable from the six public actions and `doctor`, and generic board orchestration and rendering.

Constraint: `fkst-ops` source contains **ZERO concrete repository names** and **ZERO deployment declarations**. It receives a declaration path plus machine-reference resolution inputs and never discovers targets. Its validation rejects unresolved logical references, duplicate identities, missing pins, unknown fields, and absolute machine values in logical-reference fields.

### 3.2 L-deployment: deployment repository

The deployment repository owns versioned source bindings and content pins for target/platform/engine, package composition, integration policy, target identity, logical durable/runtime/log identities, provider bindings, and its lock. The `fkst-ops` self-pinning stage is schema-agnostic: it never parses, validates, or understands the deployment schema or a provider contract.

The deployment repository can be the host repository. The website already owns a 123-line bootstrap that delegates shared orchestration (`fkst-website/scripts/run.sh:1-6,81-123`) and five tracked local-package files under `.fkst/local-packages/site-board` (command: `cd fkst-website && git ls-files .fkst/local-packages/site-board`). A satellite repository such as `trureturing-fkst` is used when a target repository must remain free of FKST content.

Multiple declarations in one deployment repository reference one repository-level lock entry rather than repeating a SHA.

### 3.3 L-machine: one configuration per machine

L-machine is generated, never hand-authored. Absolute roots and binary locations are derived below
the conventional home base, and the bot login is discovered from the authenticated GitHub CLI
session. Managed bot membership, integration branches, and cadence are deployment parameters.
The generated profile contains no authored machine defaults.

Declarations reference machine values by **logical name only**. They never contain absolute paths, logins, or secrets.

### 3.4 L-package-semantics: producing packages

Producing packages retain `*_board_fact.py`, github-devloop fact interpretation, and AVM fact interpretation. The current multi-repository board calls package-owned workflow and lifecycle fact producers at `.claude/skills/dogfood-github-devloop/dogfood_board.sh:57-102`. A consumer must never own producer semantics.

### 3.5 Partition rule

A fact is **deployment truth** when independently deployable units intentionally choose it: pins, composition, integration policy, target identity, and provider selection. A fact is **machine truth** when it is placement or credential: absolute paths, login, bot set, or secrets.

An integration branch is deployment policy when the deployment chooses it and a machine default otherwise. Explicit deployment policy overrides the referenced machine default.

## 4. Minimal Typed Declaration Schema

The schema below reproduces the current `packages`, `substrate`, and `website` topology without any target name in `fkst-ops`. The current `cfg()` produces `REPO`, `HOST`, `PKGSRC`, `DUR`, and `LOCAL_PKGS` at `.claude/skills/dogfood-github-devloop/dogfood.sh:78-99`; launch consumes them at `:366-405`, while status and sync consume them at `:499-528,777-800`.

```toml
schema = "fkst.ops.deployment.v1"

[[deployment]]
id = "<repository-local stable id>"
target_identity = "<provider-specific opaque identity>"

[deployment.claim_posture]
mode = "<assignee|label>"
label_exclusive = <true|false>

[deployment.github_devloop_profile]
version = "<required profile data version>"
id = "<required deployment-selected profile id>"
data = { "<producer-defined key>" = "<producer-defined value>" }
producer_binding = "<deployment.providers.board_github_control binding id>"

[deployment.sources.target]
lock_ref = "<repository-level external-source id>"
[deployment.sources.platform]
lock_ref = "<repository-level external-source id>"
[deployment.sources.engine]
lock_ref = "<repository-level external-source id>"

[deployment.packages]
platform = ["<package name>"]
host = ["<package name>"]

[deployment.integration]
upstream_branch = "<branch>"
integration_branch = "<branch or machine-default reference>"
rollup_merge = "<profile-supplied policy value>"

[deployment.machine]
target_checkout = "<logical root>"
platform_checkout = "<logical root>"
engine_checkout = "<logical root>"
engine_binary = "<logical binary>"
durable = "<logical durable identity>"
runtime = "<logical runtime identity>"
logs = "<logical logs identity>"
rate_pool = "<logical rate-pool identity>"
bot_login = "<logical credential>"
managed_bot_set = "<logical set>"

[deployment.providers]
engine = "<provider binding id>"
board_engine_durable = "<provider binding id>"
board_github_control = "<provider binding id>"

[[provider]]
id = "<repository-local binding id>"
kind = "<engine|board.engine-durable|board.github-control>"
implementation = "<source lock id>:<relative executable entry point>"
contract = "<closed contract version>"
configuration = { build_command = ["<executable>", "<argument>"] } # engine; board configurations are empty
```

| Field | Type and rule | Current consumer anchor |
|---|---|---|
| `schema` | Required closed schema identifier | New validation contract |
| `deployment.id` | Required unique local identifier | Names log/runtime instances and selects entries at `dogfood.sh:108-109,368-369,822-833` |
| `target_identity` | Required opaque provider identity | `REPO` drives launch and board queries at `dogfood.sh:396` and `dogfood_board.sh:69-100,173-197` |
| `claim_posture` | Required closed deployment policy: `mode` is `assignee` or `label`; `label_exclusive` is boolean. Omission is invalid, so the process cannot inherit platform defaults | Exported at supervise launch and reported by `status` from the running process log |
| `github_devloop_profile` | Optional profile block; when present, `version`, `id`, and `producer_binding` are required. `producer_binding` is the deployment's existing pinned `board.github-control` binding. The declaration supplies `version`, `id`, and deployment-owned values/references in opaque `data`; that binding supplies its contract-versioned producer-owned semantic data and owns interpretation | Owned outside `fkst-ops`; current defaults and exports are at `dogfood.sh:53-61,396-401` |
| `sources.*.lock_ref` | Required repository-level content-pin reference; entries can be shared | New declaration contract; the working repository-level pin shape is `fkst-website/fkst.lock:1-10` |
| `sources.*.git` | Resolved Git URL copied from the referenced repository-level lock entry; not declared separately | Restores corrupt run checkouts as implemented by `ensure_run_checkout` at `dogfood.sh:173` and invoked at `:466-467`; the lock URL shape is `fkst-website/fkst.lock:1-3` |
| `packages.platform` | Required non-empty string list | Derived and passed at `dogfood.sh:101-106,369-378` |
| `packages.host` | Optional string list, default empty | Conditionally passed at `dogfood.sh:381` |
| `integration.upstream_branch` | Required branch | Consumed by freshness, CI, and sync at `dogfood.sh:266-303,717-738,747-782` |
| `integration.integration_branch` | Required branch or machine-default reference | Consumed at `dogfood.sh:198-224,463-472` |
| `integration.rollup_merge` | Required value supplied by the optional profile when that profile applies, otherwise explicit deployment policy | New declaration contract; the current value is exported at `dogfood.sh:398-399` |
| `machine.target_checkout` | Required logical root | Resolves `HOST`; used at `dogfood.sh:108,375,467-472,507` |
| `machine.platform_checkout` | Required logical root | Resolves `PKGSRC`; used at `dogfood_board.sh:58,82` and `dogfood.sh:369-378,466-472` |
| `machine.engine_checkout` | Required logical root | Resolves `SUBSTRATE_SRC`; used at `dogfood.sh:262-304` |
| `machine.engine_binary` | Required logical binary | Resolves `BIN`; exported at `dogfood.sh:396` |
| `machine.durable` | Required logical identity | Resolves `DUR`; passed at `dogfood.sh:378` |
| `machine.runtime` | Required logical identity | Current launch creates a runtime root at `dogfood.sh:366-380` |
| `machine.logs` | Required logical identity | Resolves `LOGDIR`; used at `dogfood.sh:109,368,405` |
| `machine.rate_pool` | Logical reference required when named by profile data | Exported at `dogfood.sh:401` |
| `machine.bot_login` | Logical credential reference required when named by profile data | Consumed at `dogfood_board.sh:73-76,97-100` and `dogfood.sh:396` |
| `machine.managed_bot_set` | Logical set reference required when named by profile data | Consumed at `dogfood_board.sh:75-76,99-100` and `dogfood.sh:399` |
| `providers.*` | Exactly one binding of each required kind per deployment | New provider-binding contract; the two current board planes are evidenced at `scripts/board.py:43-50,528-542` and `dogfood_board.sh:9-16,67-116` |
| `provider.implementation` | Required `<source lock id>:<relative executable entry point>`; absolute entry points are rejected | New provider-binding contract; current concrete producer lookup is at `dogfood_board.sh:57-102` |
| `provider.contract` | Required closed contract version matching its kind | New provider-binding contract |
| `provider.configuration` | Required closed table typed by provider kind; engine requires non-empty `build_command: [Arg]`, board configurations are empty | Validated at `schema/validator.py:provider-binding-configuration`; consumed at `ops/deployment_operator.sh:engine-provider-configuration` and `host/bin_bootstrap.sh:engine-provider-configuration` |

Each referenced lock entry contains a Git URL, `resolved.rev`, and `tree_sha256`; the website lock has this shape at `fkst-website/fkst.lock:1-10`. Library export hashes are optional (`fkst-website/fkst.lock:12-15`) and do not replace source-tree verification.

Write posture and claim posture intentionally have different owners. Write posture answers whether
this host may mutate GitHub for this run; it is a reversible operational stance, so
`FKST_GITHUB_WRITE` remains a host fact an operator may flip between launches. Claim posture answers
how this deployment coordinates ownership with peer deployments. It must remain stable for the
deployment lifetime and peers must agree, so it is required deployment policy and cannot fall back
to an inherited environment default.

### 4.1 Launch environment classification

This table audits every environment variable that `ops/deployment_operator.sh` reads to resolve a launch or
sets on the supervise command. Variables subsequently constructed inside the pinned host-run
contract (for example `FKST_PROJECT_ROOT`, `FKST_RUNTIME_ROOT`, and `FKST_DURABLE_ROOT`) are derived
launch arguments, not additional operator inputs.

| Variable | Classification | Source |
|---|---|---|
| `FKST_OPS_DECLARATION` | host fact | Host-selected path to the declaration artifact |
| `FKST_OPS_MACHINE_PROFILE` | host fact | Host-selected path to machine placement and credentials |
| `FKST_OPS_LOCK` | host fact | Host-selected path to the deployment lock artifact |
| `PYTHONPATH` | discovered/derived | Prepends the physical mechanism checkout for validator loading |
| `BIN` | discovered/derived | Resolved from a declared logical binary through the machine profile |
| `FKST_GITHUB_REPO` | declared parameter | `deployment.target_identity` |
| `FKST_GITHUB_WRITE` | host fact | Per-run reversible operator posture; validated as `0` or `1` |
| `FKST_GITHUB_CREDENTIAL_HELPER` | discovered host fact | Absolute executable path, supplied to the cadence service environment. Each invocation prints exactly one JSON object containing `login` and a newly issued `token`. The helper path is not a secret; its output is. The output is held in memory only and is never placed in a declaration, artifact, log, status field, or process argument. |
| `GH_TOKEN` | ephemeral per-call fact | Set only in the real `gh` child's environment after the helper's returned login equals the declared bot. It is never inherited by supervise and is replaced on every GitHub call. |
| `FKST_GITHUB_WRITER_LOGIN` | discovered/derived | Login fact parsed from the authenticated CLI session's active-account report together with its credential source; recorded without the credential in the supervise startup log and reported by `status` |
| `FKST_GITHUB_CLAIM_MODE` | declared parameter | `deployment.claim_posture.mode` |
| `FKST_GITHUB_CLAIM_LABEL_EXCLUSIVE` | declared parameter | Boolean `deployment.claim_posture.label_exclusive`, encoded as `0` or `1` |
| `FKST_RATE_POOL_ROOT` | host fact | Machine-profile resolution of `deployment.machine.rate_pool` |
| `FKST_GITHUB_BOT_LOGIN` | host fact | Machine-profile credential resolution of `deployment.machine.bot_login` |
| `FKST_DEVLOOP_MANAGED_BOT_LOGINS` | declared parameter | Deployment membership, checked against the resolved machine set |
| `FKST_DEVLOOP_UPSTREAM_BRANCH` | declared parameter | `deployment.integration.upstream_branch` |
| `FKST_DEVLOOP_INTEGRATION_BRANCH` | declared parameter | Declared branch, optionally resolved through a named machine default |
| `FKST_DEVLOOP_ROLLUP_MERGE` | declared parameter | `deployment.integration.rollup_merge` |
| `FKST_OPS_GITHUB_DEVLOOP_PROFILE` | declared parameter | Resolved `deployment.github_devloop_profile` |
| `FKST_WORKTREE_GC_REMOVE` | discovered/derived | Mechanism-owned fixed launch behavior (`1`) |

No variable in this launch boundary is unclassified after the claim-posture addition.

### 4.2 Provider port contracts

These are direct executable ports, not a plugin framework. A declaration binds exactly one provider of each kind; the single board front-end invokes both board bindings and rejects missing, duplicate, or version-mismatched bindings before execution.

<a id="provider-source-resolution"></a>
**Provider source resolution.** `provider.implementation` is `<pinned-source-id>:<safe-relative-entry>`. The pinned source ID must be bound either by that deployment's `target`, `platform`, or `engine` source, or by the pin-verified `fkst-ops` mechanism checkout that is executing preflight. The entry's full revision and canonical tree verification establishes the mechanism root before preflight; preflight resolves only inside that verified checkout. Absolute paths, traversal, checkout escape, missing pins, and missing or non-executable entries fail closed.

<a id="published-provider-surface"></a>
**Published provider surface.** Mechanism-owned provider entry points are a public contract surface, explicitly declared by relative path and provider kind in `schema/provider_surface.py` at `provider-published-surface`. No other file in the mechanism checkout is bindable merely because it exists or is executable. Preflight enforces this allowlist at `provider-mechanism-source-root`; a path absent from it, or published for a different kind, fails closed. The published entries are `providers/engine.py` for `engine`, `providers/board_engine_durable.py` for `board.engine-durable`, and `providers/board_github_control.sh` for `board.github-control`.

Every port uses one shared minimal envelope, `fkst.ops.invocation.v1`. Input is exactly one UTF-8 JSON document on stdin: `{"version":"fkst.ops.invocation.v1","contract":"<ContractId>","input":{}}`. Output is exactly one UTF-8 JSON document on stdout, either `{"version":"fkst.ops.invocation.v1","ok":true,"result":{}}` or the exact failure encoding `{"version":"fkst.ops.invocation.v1","ok":false,"failure":{"code":"<FailureCode>","message":"<String>","details":{}}}`; the `input`, `result`, and `details` objects carry contract-defined members, and `details` is present even when empty. Stderr is diagnostic only and is never parsed as result data. Exit `0` is required only for the success object. Exit `2` denotes contract or input failure; exit `1` denotes provider operation failure. Any other exit, malformed JSON, missing output, multiple JSON documents, an exit/object mismatch, or output that violates the selected contract is a port failure.

The self-pinning entry preserves the declaration path and machine-reference resolution inputs without parsing either during hydration. Pinned `fkst-ops` resolves the logical references, validates the resolved declaration and every provider contract, and passes resolved concrete values to each port; no port accepts or resolves a logical machine name.

<a id="declaration-validation-vs-action-preconditions"></a>
**Declaration validation versus action preconditions.** Declaration validation establishes well-formedness, not readiness for every engine action. The resolved `machine.engine_binary` is a non-empty absolute build path and may be absent or non-executable while the declaration remains valid. Validation retains all other pre-existence checks: target, platform, and engine checkout roots; the durable root; platform and host package roots; and provider entry executables. Providers do not create those inputs.

<a id="engine-binary-consumption-precondition"></a>
**Engine-binary consumption precondition.** Immediately before an action runs or observes through the engine, the operator requires the declared build path to be an existing executable file and fails with a typed diagnostic naming that path when it is unavailable. Engine build and sync may reach the engine provider before this check because that provider creates the declared output. The engine provider result and board consumption remain typed as `ExistingExecutable`.

| Kind / contract | Typed input | Typed result | Typed failures |
|---|---|---|---|
| `engine` / `fkst.ops.engine.v1` | `{engine_checkout: ExistingGitRoot, engine_binary: AbsoluteBuildPath, expected_branch: Branch, operation: build, build_command: [Arg]}` | `{binary: ExistingExecutable, source_rev: FullGitSha}` | `INVALID_INPUT`, `CHECKOUT_MISSING`, `CONTRACT_MISSING`, `WRONG_BRANCH`, `UPDATE_FAILED`, `BUILD_FAILED` |
| `board.engine-durable` / `fkst.ops.board.engine-durable.v1` | `{engine_binary: ExistingExecutable, durable_root: ExistingRoot, cache: ConcretePath, refresh: Bool, ttl_seconds: NonNegativeInt, stall_seconds: NonNegativeInt}` | `{view: "engine-durable", rows: [BoardRow], health: BoardHealth}` | `INVALID_INPUT`, `OBSERVE_FAILED`, `CACHE_FAILED`, `MALFORMED_FACT` |
| `board.github-control` / `fkst.ops.board.github-control.v1` | `{target_identity: OpaqueIdentity, platform_checkout: ExistingGitRoot, profile: AssembledResolvedProfileData, bot_login: ConcreteLogin, managed_bot_set: [ConcreteLogin]}`; producer-owned profile data is versioned by this pinned binding and contract | `{view: "github-control", rows: [BoardRow]}` | `INVALID_INPUT`, `AUTH_FAILED`, `FETCH_FAILED`, `PRODUCER_FAILED`, `MALFORMED_FACT` |

`BoardRow` is `{key: String, classification: String, fields: Map<String, Scalar>}` and `BoardHealth` is `{status: String, anomalies: [BoardRow]}`. Providers return one typed result or one typed failure and never print an untyped success value. The engine contract reflects the present engine build at `scripts/run.sh:792-815`; the board contracts reflect the distinct current inputs and renderers at `scripts/board.py:43-50,528-542` and the GitHub label/comment producers at `dogfood_board.sh:9-16,67-116`.

The engine input has one deliberate addition to the earlier four-field design: `build_command` is required as a non-empty argv list and is executed directly without a shell. The committed engine provider binding carries this deployment truth in its typed `configuration.build_command`; machine profiles have no command namespace or second carrier. Validation fails closed when the engine binding omits it. The resolved binding is wired into both live callers at `ops/deployment_operator.sh:engine-provider-configuration` and `host/bin_bootstrap.sh:engine-provider-configuration`, while `providers/engine.py:engine-contract-input` remains generic and owns no producer or build-system names.

## 5. Semantic Extraction Boundary

The extraction is cut by semantic responsibility, not filename.

### 5.1 Remains in fkst-packages

`check`, `test`, `test-affected`, and `test-composed` remain in `fkst-packages` with their local ratchets and helpers. Their current dispatch is at `scripts/run.sh:818-849`. CI remains untouched and invokes only `scripts/run.sh test` at `.github/workflows/ci.yml:99-101`.

### 5.2 Moves to fkst-ops

Generic lifecycle execution and observation reachable from the six public actions move to `fkst-ops`. The existing `doctor` command moves as a separately invocable operator entry with unchanged behaviour, fail-visible accounting, sourced-shell/helper closure, and separate tests; it is not called by a public-action implementation, and its invocation timing remains externally owned.

Engine build is owned by the engine repository and invoked by `fkst-ops` through the declared `engine` provider. The provider receives the resolved concrete engine checkout and binary. `fkst-packages`' `cmd_build` is deleted, not moved: it locates an fkst-substrate checkout, hardcodes `fkst-substrate` as a fallback, rejects a branch other than `dev`, pulls, and builds `fkst-framework` at `scripts/run.sh:792-815`.

### 5.3 Resolved closure inventory

The moving closure is the operator layer (`dogfood.sh`, `dogfood_board.sh`, `workspace_manifest.py`, `dead_letter_causes.py`), the host-serving platform layer (`bin_bootstrap.sh`, `host_run.sh`, `host_entry.sh`, `bin_cache.py`), the observation/render layer (`board.py`, `avm_scoreboard.py`, `doctor.sh`), and their enumerated tests and fixtures (`addendum.md:35-52`). `scripts/run.sh` currently sources the three shell files unconditionally at `scripts/run.sh:83-88`.

The observation layer contains an **ASSUMED-UNVERIFIED** duplication: `scripts/avm_scoreboard.py` (677 lines) and `packages/github-devloop-ops/departments/observability/avm_scoreboard.lua` (699 lines) both aggregate autonomy-ledger facts into L0-L4 AVM scoreboard rows and carry `avm_rate`, `revert`, `false_consensus`, and `cost_per` vocabulary. Before any observation-layer move, run the bounded behaviour differencing mandated by Section 6 on this pair and record the outcome. If the package-owned implementation covers the Python implementation's semantics, do not carry the Python implementation into `fkst-ops`; bind the `board.engine-durable` provider to the package-owned semantics. Neither implementation is deletable until the differencing result is recorded.

`scripts/run.sh host` through `host_entry.sh` is one published platform entry used by external host repositories for both correctness and supervision: the website delegates `check` through it at `fkst-website/scripts/run.sh:91-102` and delegates `test|supervise` at `:117-122`; `host_entry.sh` dispatches host `check`, `test`, and `supervise` at `scripts/host_entry.sh:615-632`.

The measured call graph over `scripts/bin_bootstrap.sh:1-200`, `scripts/host_run.sh:1-722`, and `scripts/host_entry.sh:1-642` contains 60 functions: 42 are reachable from the run face, 51 from the host test/check face, 33 are shared, and zero are reachable from neither. Splitting by face duplicates 33 functions, so all three files move whole and none of their functions is stripped as dead code. `fkst-packages`' own `cmd_check`, `cmd_test`, `cmd_test_affected`, and `cmd_test_composed` call zero of those 60 functions (`addendum.md:63-67`), so the cut does not disturb its CI face. The only remaining couplings are run-face test registrations and the dogfood boundary ratchet, and both are deletion-only edits (`scripts/run.sh:202-222`; `scripts/check_repo_config.py:36-38`; `scripts/check_repo_runner.py:263-264`).

Migration must repoint both published consumers in `fkst-website/scripts/run.sh:91-102,117-122` to pinned `fkst-ops` in the same website cutover. After the layer moves, `fkst-packages` no longer provides `run.sh host`; repointing only `test/check` or only `supervise` breaks the unrepointed consumer.

## 6. Exactly One Board Front-End

The canonical operational face is one consolidated board front-end. It binds the `board.github-control` provider for GitHub labels, comments, workflow facts, and lifecycle facts (`dogfood_board.sh:9-16,67-116`) and the `board.engine-durable` provider for engine `observe`, durable health, anomalies, and AVM false-consensus views (`scripts/board.py:43-50,528-542`). Both planes are required and an implementer cannot drop either one.

`fkst-ops` owns only generic orchestration and rendering. Fact producers stay package-owned and are consumed through declared providers/ports.

The front-end always renders the healthy plane. A failed plane is rendered as an explicit named failure row in that plane; it is never silently dropped. Any provider failure makes the front-end exit nonzero, including when the other plane renders successfully.

Before consolidation, perform bounded behaviour differencing of `scripts/board.py` and `scripts/avm_scoreboard.py` against `dogfood_board`:

1. Enumerate input facts, filters, grouping, derived states, output sections, failure behaviour, and CLI options.
2. Bind every item to either `engine-durable` or `github-control` and name every distinct result as an explicit view.
3. Merge generic rendering into the single front-end and keep package-specific interpretation behind producer ports.
4. Treat any unbound or unnamed view as a defect.
5. Drop a view only through an explicit named design decision recorded by revising this specification; implementation discretion cannot drop a view.

The differencing verifies binding and view names; it does not decide which plane to delete. The second front-end is deleted after per-deployment cutover and Section 2's removal condition, while both provider planes remain.

## 7. Self-Pinning Entry

There is one configuration set per **deployment repository**, not a copied bootstrap. The deployment owner invokes any available `bin/fkst-ops` with its deployment directory; that entry is the versioned, self-pinning trust root and performs exactly three duties, in order:

1. locate its repository-owned lock;
2. hydrate pinned `fkst-ops` and verify its full `resolved.rev` and canonical `tree_sha256`;
3. re-execute pinned `fkst-ops` with the original arguments.

The entry determines pinned state from the physical checkout containing the running script. It proceeds directly only when that checkout's full `HEAD` and canonical tree hash exactly match the deployment lock. An environment sentinel is never sufficient. Otherwise it reuses or hydrates the checkout named by the requested revision, runs preflight before promotion, atomically updates the current pointer, and re-executes the pinned entry. A stale `current` pointer is not an error when the lock advances. A bounded re-exec depth is set only for candidate preflight and handover; an entry reached at that depth which is not physically pinned fails closed rather than hydrating recursively.

The canonical tree verifier comes exclusively from the checkout containing the currently invoking entry. That one verifier computes and diagnoses the hashes of the invoking root, cached checkouts, existing verified destinations, and fresh candidates; verifier code from a candidate or cache is never executed before that checkout passes independent verification.

The ordered handover is one total fail-closed declaration preflight within `bin/fkst-ops`. Its pinning stage verifies only the mechanism pin and tree and never parses, validates, or understands the declaration schema or any provider contract. The pinned stage resolves machine references and exclusively validates the resolved schema, provider contracts, checkout roots, package roots, and provider executable entry points. Outputs created by providers, including the engine binary build path, are action preconditions rather than declaration-preflight pre-existence requirements. Nothing in deployment operational state mutates until pinned `fkst-ops` completes all declaration validation successfully.

The current lock records both at `fkst-website/fkst.lock:8-10`, but the bootstrap parses only `resolved.rev` at `fkst-website/scripts/run.sh:32-47` and checks it out at `:64-78`. It does **not** verify `tree_sha256` or `exports_sha256` (command: `cd fkst-website && rg -n 'tree_sha256|exports_sha256|resolved.*rev' scripts/run.sh`). Generation 1 closes the tree-hash gap.

Canonical `tree_sha256` is `"sha256-" + lowercase_hex(SHA-256(stream))`, where `stream` is the concatenation, in raw-byte path order, of every tracked blob at `resolved.rev`, each framed as unsigned 64-bit big-endian length plus bytes for path, Git mode, and SHA-256(blob bytes). `git ls-tree -r -z --full-tree resolved.rev` supplies path and mode; `.git` and untracked files are absent by construction. This framing follows the repository's existing canonical tracked-tree practice at `scripts/intent_bounded_replay/semantic_tree.py:69-94,143-167` and is a new lock contract.

Preflight failure is non-mutating: declaration validation failure, a missing referenced logical name, a missing checkout/package root or required contract, clone/checkout failure, `HEAD` mismatch, or tree-hash mismatch exits nonzero before any operational process, Git mutation of a resolved working checkout, durable mutation, or cache-pointer mutation. Acquisition occurs in a new sibling temporary directory; failure removes only that partial directory and preserves the last verified checkout and cache pointer. The self-pinning entry verifies `HEAD` and tree hash in the temporary directory, hands that unpromoted checkout to pinned `fkst-ops`, and replaces the cache pointer with one rename only after pinned `fkst-ops` completes all validation successfully. A cached checkout is verified before every handover. Tests cover positive fresh, cached, and already-pinned paths, `resolved.rev` mismatch, tree-hash mismatch, acquisition failure, preservation of last-known-good, and cleanup of partial state; pinned-`fkst-ops` tests cover missing machine names/contracts/roots and resolved-schema and provider-contract failures. The full preflight tests assert zero operational or cache-pointer mutation across both stages on every failure.

A globally installed unpinned launcher is rejected because it breaks versioned reproducibility. A smaller adapter is permitted only after two deployments demonstrate identical producer-declared public-action behaviour.

## 8. Lift, Do Not Rewrite

Port proven `dogfood.sh` behaviour. The only surgical changes are replacing the hardcoded `cfg()` cases at `.claude/skills/dogfood-github-devloop/dogfood.sh:82-99` with validated declaration input, and replacing concrete paths with declared source/package roles.

github-devloop semantics are an **explicit optional versioned data profile** owned outside `fkst-ops`, not universal engine core. There is no built-in default. When the profile block is present, the deployment declaration supplies its required `version` and `id`, deployment-owned values/references, and `producer_binding`, which is the existing pinned `board.github-control` binding. That binding supplies its contract-versioned producer-owned semantic data and owns interpretation. The `board.github-control` port receives the assembled resolved profile data. `fkst-ops` only shape-validates, stores, and passes through the profile; it never supplies defaults or interprets or hardcodes profile values.

The producer port owns profile interpretation, including label-state meaning and authorization/bot-claim semantics. Current defaults and exports are at `.claude/skills/dogfood-github-devloop/dogfood.sh:53-61,396-401`; current label-state interpretation is at `.claude/skills/dogfood-github-devloop/dogfood_board.sh:225-278`.

A language rewrite is out of scope.

## 9. Producer-Declared Public-Action Equivalence Acceptance

`fkst-ops-compare` is a bounded executable gate for the producer-declared public-action surface and cutover authorization contract. Before every invocation, it independently seeds or namespaces per side every mutable or observable fixture resource: source checkout, runtime root, durable root, log directory, cache paths, and process namespace. It runs the old entry once against one side and the new entry once against the other, enforces a per-invocation timeout, and retains both sides until comparison completes. Cross-side visibility is forbidden. A comparison run that cannot demonstrate this per-side isolation is a gate failure, not a pass. This reset is mandatory for every cell, especially stateful `restart` and `sync` cells. Provider and network inputs come from versioned deterministic fixtures; live network access is disabled.

The closed observation record is `{fixture_id, action, argv, provider_fixture_ids, seed_refs, exit_code, stdout_normalized, stderr_normalized, git_before, git_after, runtime_before, runtime_after, durable_before, durable_after, process_state_before, process_state_after}`. Normalization removes only timestamps, PIDs, declared absolute-root prefixes, and ANSI styling. A cell passes only when exit codes match and every action-specific field below compares equal; the gate exits `0` only when every matrix cell passes, `1` for any mismatch, and `2` for fixture/setup/comparator error. The deployment cutover record stores the complete observations and comparator version. This is a comparator plus fixture matrix, not a proof apparatus.

| Action | Equivalent means | Mechanical check |
|---|---|---|
| `board` | Same targets, visible items/views, classifications, and explicit fetch/provider failures | Compare normalized row-key/classification sets; require identical named views and failure parity |
| `status` | Same running state, target, code revisions, panic count, and readiness-relevant state | Compare parsed fields exactly; ignore PID and uptime value while requiring uptime for running processes; require identical Git, runtime, durable, file, and process state before and after each invocation |
| `logs` | Same latest logical supervise log and tail semantics | Seed two logs; compare selected identity and exact final N normalized lines for default and explicit N |
| `restart` | Same source sync, prior-process replacement, durable reuse, composition, readiness, and failure exit | Compare PID replacement, durable identity, loaded revisions, readiness markers, and exit code |
| `sync` | Same advancement, engine freshness, stale class, restart decision, and dirty/diverged refusal | Run current, stale-package, stale-engine, dirty, and diverged fixtures; compare final refs, build/restart decisions, and exit code |
| `stop` | Same stopped/running handling, SIGKILL result, exhaustive all-target attempts, and aggregate failure exit | Run stopped and running fixtures; compare attempted targets, process state, output, and exit code |

The required producer-declared public-action matrix is finite and the gate runs every cell: `board` has both-healthy, engine/durable-failed, GitHub-failed, and both-failed cells; `status` has stopped and running cells; `logs` has default-N, explicit-N, and multiple-candidate selection-identity cells; `restart` has success and failure-with-rollback cells; `sync` has current, stale-package, stale-engine, dirty, and diverged cells; `stop` has stopped and running cells. Each board failure cell verifies that every healthy plane is rendered, every failed plane has its explicit named failure row, and any provider failure exits nonzero. A missing required cell is gate failure.

Current status observation is at `.claude/skills/dogfood-github-devloop/dogfood.sh:499-509` and its dispatch is at `:829`. The current doctor implementation is at `:710-720` and its separate dispatch is at `:830`.

`doctor` has a separate deterministic closed acceptance suite outside public-action equivalence. Each fixture invokes the `doctor` entry and compares the exact normalized observation and fail-visible accounting. Age-based fixtures use one fixed fixture clock, and process elapsed times and receipt mtimes are seeded relative to that clock:

| Capability | Setup | Exact observation compared | Pass rule |
|---|---|---|---|
| Stray supervise detection (`dogfood.sh:602`) | Seed one declared supervise and one live undeclared supervise with stable fixture identities | Ordered reported stray identity set and sweep finding count | Only the undeclared supervise is reported, the count is `1`, no process state changes, and the sweep result is fail-visible |
| Guarded leaked-test reaping (`dogfood.sh:633`) | Seed one over-budget orphaned test process group and one otherwise matching over-budget group whose parent is live and non-orphan | Ordered candidate, guarded-skip, and reaped identity sets plus before/after liveness | Only the orphaned group is reaped, the live-parent group remains alive and is recorded as a guarded skip, and accounting names both decisions |
| Stale receipt cleanup (`dogfood.sh:696`) | Seed one expired temporary receipt and one current receipt with fixed contents and mtimes | Ordered removed/preserved receipt identities and before/after receipt-tree hash | Only the expired receipt is removed, the current receipt and its bytes remain, and accounting records one cleanup |
| Durable health failure (`dogfood.sh:567`) | Supply a deterministic `observe --json` failure for one declared durable identity | Durable identity, provider exit classification, normalized diagnostic, and sweep failure count | The health failure is named exactly, no durable/process/file state changes, and the sweep exits/reports failure visibly |

Live production behaviour of the current dogfood path was not exercised by this read-only review and is **ASSUMED-UNVERIFIED**. First-adopter acceptance and soak supply that evidence.

Contract tests scan **all Git-tracked paths in `fkst-ops`** for a versioned denylist containing the concrete repository names from the first adoption. Test configuration contains one exact, explicit path enumeration for every exclusion, including this design document, each intentional concrete-name fixture, and each named generated path. Category-only, directory, glob, implicit, and otherwise unenumerated exclusions are forbidden and are gate failure. Every other tracked source, configuration, and build artifact is scanned, and any literal match fails. An N+1 fixture copies the `fkst-ops` tree, adds only a deployment declaration and lock in a separate deployment fixture, invokes every producer-declared public action with deterministic providers, and asserts the copied `fkst-ops` tree hash is unchanged. This executable scan and N+1 test verify the zero-name and zero-source-change contracts; concrete names occur only at explicitly enumerated excluded paths.

## 10. Cutover: No Dual Mode

"Build new before deleting old" is an ordering constraint, not permission for permanent duplication. Each deployment uses one authoritative invocation marker: a single repository-owned pointer file naming the active pinned entry. Cutover ordering is acceptance on the candidate, atomic replacement of that one pointer, post-switch execution of the producer-declared public-action smoke matrix, then read-only freeze of the old entry. The old pointer and checkout remain as last-known-good until Section 2 permits removal.

The pointer replacement writes and verifies a sibling temporary file, then atomically renames it over the authoritative pointer; no second marker is created. On interruption before rename, the old pointer remains authoritative and resume deletes the temporary file and restarts acceptance. On interruption after rename, the new pointer is authoritative and resume runs post-switch verification. A failed post-switch verification atomically restores the preserved old pointer and records failure; retry starts from acceptance. Every state has exactly one authoritative entry, never zero or two.

There is no deprecated shim, compatibility mode, opt-in switch, or dual-write path.

## 11. RAPTOR Deletion Checklist

This generation deletes:

- [ ] The hardcoded three-target `cfg()` block; never copy it into `fkst-ops` (`dogfood.sh:82-99`).
- [ ] `scripts/check_repo_dogfood_boundary.py`; it names the skill script and requires `scripts/run.sh supervise` solely to police the current seam at `scripts/check_repo_dogfood_boundary.py:10-14,96-123`.
- [ ] The second board front-end, after Section 6 binds and names every view and the per-deployment removal condition fires; delete neither provider plane.
- [ ] Public commands outside the six-action cutover contract, except the retained invocable `doctor` entry; retain private primitives required by the six actions and `doctor`.
- [ ] Any coupling from `status` or another public-action implementation to `doctor`; retain `doctor`, its externally owned invocation timing, fail-visible accounting, and closed acceptance suite.
- [ ] `fkst-packages`' `cmd_build`; engine build is reached only through the declared engine provider (`scripts/run.sh:792-815`).

At cutover, additionally delete after the removal condition fires:

- [ ] the old dogfood skill copy;
- [ ] the `host_run` / `host_entry` run face;
- [ ] obsolete run-face dispatch in `scripts/run.sh`.

The current `host`, `run`, and `supervise` dispatch entries are at `scripts/run.sh:824-852`; `run.sh` sources both run-face files at `scripts/run.sh:85-88`.

## 12. fkst-hostctl: Absorb Nothing

`fkst-hostctl` contributes no code, architecture, or capability. It remains untouched as sunk prior art.

Its tracked repository contains 60,058 lines total. On the tracked-file basis, `lib` is 16,622, `tests` 25,681, `docs` 10,046, and `tools` 6,863; these directories total 59,212, while all tracked files total 60,058 (commands: `cd fkst-hostctl && git ls-files -z | xargs -0 wc -l | tail -1`; `git ls-files -z lib tests docs tools | xargs -0 wc -l`; untracked files excluded).

launchd autostart, scheduled maintenance, adoption records, and WAL-transactional pin advance are separate purposes outside success criteria. If an observed failure later requires one, record the requirement and a behavioural test from that failure, then implement the smallest generic capability at its natural layer. Prior code can be read as research and is never adopted.

## 13. Non-Goals

- A language rewrite or bootstrap framework.
- A global unpinned launcher.
- Permanent duplication, compatibility mode, deprecated shims, or migration switches.
- Changing `fkst-packages` CI or moving its check/test family.
- Moving package-owned fact semantics into `fkst-ops`.
- Deleting or downgrading the invocable `doctor` entry or its sweep behaviour.
- Target discovery, repository names, or deployment declarations in `fkst-ops`.
- Any `fkst-hostctl` code, architecture, or capability.
- An equivalence proof apparatus.
- A scheduling system, launchd integration, adoption records, or transactional pin advancement.

## 14. Open Items

1. **Board view names.** Complete Section 6 differencing, bind every view to one of the two required providers, and record its stable name. Dropping a view requires an explicit revision to this specification.
2. **Live production behaviour — ASSUMED-UNVERIFIED.** The read-only review did not exercise it; `packages` acceptance and soak must.
3. **Soak duration — ASSUMED-UNVERIFIED.** The owner records a finite duration before `packages` cutover; removal triggers are fixed by Section 2.
4. **AVM implementation equivalence — ASSUMED-UNVERIFIED.** Before the observation layer moves, run and record Section 6 bounded behaviour differencing for `scripts/avm_scoreboard.py` and `packages/github-devloop-ops/departments/observability/avm_scoreboard.lua`. Bind `board.engine-durable` to the package-owned semantics and omit the Python implementation from `fkst-ops` only when the result demonstrates semantic coverage; delete neither implementation before that result.

No other design decision is open. Ownership direction, first adopter, self-pinning entry location, no-dual-mode rule, optional-profile boundary, deletion set, and `fkst-hostctl` disposition are fixed.

⟦AI:FKST⟧
