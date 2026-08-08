# fkst-ops

`fkst-ops` is the repository-agnostic operational mechanism for FKST
deployments. It exists because operational behavior was extracted from the
repositories it operates: a deployment repository can now contain configuration
and pins, while this repository contains the Bash and standard-library-only
Python executor, validation, lifecycle, and observation code. The
[extraction design](docs/superpowers/specs/2026-08-08-fkst-ops-extraction-design.md)
is the authoritative specification.

This is not a deployment repository, a target registry, or a plugin framework.
It contains no deployment declaration for a concrete repository. It does not
own package-produced facts or machine paths and credentials. No deployment has
cut over from the previous operator to this mechanism.

## How the repositories relate
The dependency is one-way:

```text
fkst-deployments  --pins and configures-->  fkst-ops
       |
       +--declares operations for--> fkst-packages / fkst-substrate / fkst-website
```

- **`fkst-ops` (mechanism):** generic execution, schema and provider validation,
  lifecycle operations, observation, and board orchestration.
- **`fkst-deployments` (configuration):** source bindings and content pins,
  package composition, integration policy, logical machine references, and
  provider bindings. Its lock pins each source by full revision and canonical
  tree hash.
- **Operated targets:** `fkst-packages`, `fkst-substrate`, and `fkst-website` do
  not depend on `fkst-ops`.

The boundary and dependency rule are defined in
[Purpose and Success Contract](docs/superpowers/specs/2026-08-08-fkst-ops-extraction-design.md#1-purpose-and-success-contract).

## Four ownership layers

1. **Mechanism:** this repository owns generic operational behavior and closed
   validation contracts.
2. **Deployment:** the deployment repository owns what is operated and which
   pinned sources, packages, policies, identities, and providers compose it.
3. **Machine:** an uncommitted machine profile resolves logical names to local
   paths, credentials, identities, sets, and defaults.
4. **Package semantics:** producing packages own the meaning of their facts;
   this mechanism only consumes those facts through declared provider ports.

See [Four Ownership Layers](docs/superpowers/specs/2026-08-08-fkst-ops-extraction-design.md#3-four-ownership-layers)
for the normative partition.

## What to run

Create `<deployment-repo>/.fkst/machine-profile.toml` from the deployment
repository's `.fkst/machine-profile.example.toml`, then validate the complete
binding without dispatching an action:

```sh
~/fkst-ops/bin/fkst-ops preflight --deployment-dir <deployment-repo> \
  --declaration <deployment-repo>/deployments/<name>.toml \
  --machine-profile <deployment-repo>/.fkst/machine-profile.toml \
  --lock <deployment-repo>/fkst.lock
```

Replace `preflight` with `board`, `status`, `logs`, `restart`, or `sync` to
operate the deployment. Invoke `doctor` in the same position for its separate
diagnostic and repair entry. `status` is non-mutating. `doctor` checks durable
health, detects stray supervisors, guardedly reaps leaked test processes, and
cleans stale receipts ([entry dispatch](bin/fkst-ops#L179),
[doctor reports](doctor/doctor.sh#L73)).

All four paths are required today; `--deployment-dir` does not derive the other
three. The entry reads the deployment lock, verifies the mechanism checkout by
full revision and canonical tree hash, hydrates the pinned checkout when needed,
and re-executes into it with a depth guard ([self-pinning entry](bin/fkst-ops#L15)).

## Provider binding

A deployment binds exactly one provider for each required kind: `engine`,
`board.engine-durable`, and `board.github-control`. The declaration names a
pinned source and relative implementation path. For mechanism-owned providers,
only the three path-to-kind pairs in
[schema/provider_surface.py](schema/provider_surface.py#L10) are public; file
presence and executable permission publish nothing. Preflight resolves logical
machine references and rejects invalid source, path, kind, contract, pin, and
checkout bindings ([validator](schema/validator.py#L171)).

The shipped `packages` declaration uses one checkout as target and platform;
the shipped `substrate` declaration uses a separate platform checkout and the
same pinned source and checkout for target and engine.

## Contributor gates

Keep both gates green:

```sh
python3 -m pytest -q
python3 scan/zero_target_names.py --name fkst-packages --name fkst-substrate --name fkst-website
```

The suite currently passes 146 tests and skips 21 host-fixture tests when their
named checkout environment variable is absent. The
[zero-name scanner](scan/zero_target_names.py#L32) exits `1` when a concrete
repository name occurs in a tracked, non-excluded file.

## Layout

`bin/` is the entrypoint; `ops/`, `host/`, `board/`, and `doctor/` implement the
operator paths; `providers/` contains published provider ports; `schema/` and
`bootstrap/` validate declarations and pins; `scan/`, `acceptance/`, and
`tests/` hold gates; `migration/`, `deployments/`, and `docs/` hold migration,
bundle, and design material.

## Open items

- No deployment has cut over from the previous operator.
- The five-action equivalence matrix has not run against both real entries.
- The website deployment is pending because its workspace manifest does not
  provide a non-empty package composition for its external platform source.

⟦AI:FKST⟧
