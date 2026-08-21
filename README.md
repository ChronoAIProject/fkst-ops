# fkst-ops

`fkst-ops` is the repository-agnostic operational mechanism used by FKST
deployment repositories. The current contracts and ownership boundaries are in
[SPEC.md](SPEC.md).

The repository contains a Bash and Python entry, schema validation, lifecycle
and observation commands, provider adapters, and deterministic acceptance
fixtures. Deployment parameters, source bindings, revision derivations, and the
mechanism pin live in the deployment repository;
local paths and credentials come from its machine profile.

For engine selection, the verified narrowing is specific: the set of writable
records that can select which engine executes goes from three to one, the
revision file named by `engine_revision.path` in the platform commit. The full
authority and widened-surface accounting is in [SPEC.md](SPEC.md#engine-revision-authority).

## Run it

Generate the machine profile and related control artifacts from a clean checkout
whose revision and tree match the deployment-owned mechanism pin:

```sh
<pinned-fkst-ops-checkout>/bin/fkst-regenerate <deployment-repo> \
  --bot-login <machine-actor-login> \
  --github-credential-source <github-app-or-github-cli-user>
```

The generator derives every logical root named by the declarations, hydrates
their declared sources, and atomically publishes the control generation under
`$HOME/.fkst/machine`. Do not hand-author or edit the generated profile. Inspect
the complete binding with:

```sh
~/fkst-ops/bin/fkst-ops preflight \
  --deployment-dir <deployment-repo> \
  --declaration <deployment-repo>/deployments/<name>.toml \
  --machine-profile "$HOME/.fkst/machine/profile.toml" \
  --lock <deployment-repo>/fkst.lock
```

Use `board`, `status`, `logs`, `restart`, `sync`, or `stop` in place of
`preflight` to invoke an operator action. `doctor` invokes the separate
diagnostic and repair sweep. `doctor process <deployment-id>` invokes the
typed, read-only deployment-process identity probe. Run `bin/fkst-ops` without
the required arguments to see its current usage.

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
