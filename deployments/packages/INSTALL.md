# Packages deployment installation

Install these files in the deployment repository without changing their relative layout:

| Bundle file | Deployment repository destination |
| --- | --- |
| `deployment.toml` | `.fkst/deployments/packages.toml` |
| `fkst.lock` | `fkst.lock` |
| `bootstrap/run.sh` | `bootstrap/run.sh` |
| `bootstrap/canonical_tree.py` | `bootstrap/canonical_tree.py` |
| `machine-profile.example.toml` | `.fkst/machine-profile.example.toml` |

Before first use, the owner must:

1. Commit `fkst-ops`, set its real Git URL in `fkst.lock`, and replace the all-zero `fkst-ops` revision and tree hash with that commit's full revision and canonical tree hash.
2. Review and advance the target/platform and engine pins if installation is not against the source revisions recorded in this bundle.
3. Copy `.fkst/machine-profile.example.toml` to an untracked machine-local file and replace every angle-bracket placeholder. The target and platform checkout values must name the same checkout. The durable root must retain the current store unless the owner deliberately accepts abandoning in-flight state.
4. Ensure all checkout/package roots and provider entrypoints exist and the engine binary is executable.
5. Run the pinned validator before any operator action:

   `python3 -m schema.validator .fkst/deployments/packages.toml .fkst/machine-profile.toml fkst.lock`

Invoke the deployment through `bootstrap/run.sh`; do not use a globally installed operator. The bootstrap accepts the declaration path first and passes all remaining machine-resolution arguments unchanged to the pinned `fkst-ops` entry.
