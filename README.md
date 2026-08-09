# fkst-ops

`fkst-ops` is the repository-agnostic operational mechanism used by FKST
deployment repositories. The current contracts and ownership boundaries are in
[SPEC.md](SPEC.md).

The repository contains a Bash and Python entry, schema validation, lifecycle
and observation commands, provider adapters, and deterministic acceptance
fixtures. Deployment parameters and pins live in the deployment repository;
local paths and credentials come from its machine profile.

## Run it

Create `<deployment-repo>/.fkst/machine-profile.toml` from the deployment
repository's example, then inspect the complete binding:

```sh
~/fkst-ops/bin/fkst-ops preflight \
  --deployment-dir <deployment-repo> \
  --declaration <deployment-repo>/deployments/<name>.toml \
  --machine-profile <deployment-repo>/.fkst/machine-profile.toml \
  --lock <deployment-repo>/fkst.lock
```

Use `board`, `status`, `logs`, `restart`, `sync`, or `stop` in place of
`preflight` to invoke an operator action. `doctor` invokes the separate
diagnostic and repair sweep. Run `bin/fkst-ops` without the required arguments
to see its current usage.

## Develop

```sh
python3 -m pytest -q
python3 scan/zero_target_names.py \
  --name fkst-packages --name fkst-substrate --name fkst-website
```

`bin/` contains the entry; `ops/`, `host/`, `board/`, and `doctor/` contain the
operator paths; `schema/`, `bootstrap/`, and `providers/` contain binding and
provider support; `tests/`, `acceptance/`, and `scan/` contain repository gates.
The extraction document under `docs/superpowers/specs/` is a historical design
record, not the current specification.

⟦AI:FKST⟧
