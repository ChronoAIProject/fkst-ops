# Deployment installation

This repository intentionally contains no concrete deployment declaration. From
the fkst-ops repository root, install the generic bootstrap files and templates
in the deployment repository:

```sh
cp -R bootstrap/ <deployment-repository>/bootstrap/
cp .fkst/deployment.example.toml <deployment-repository>/deployment.toml
cp schema/examples/machine-profile.example.toml <deployment-repository>/.fkst/machine-profile.toml
chmod +x <deployment-repository>/bootstrap/run.sh
```

Keep the machine profile untracked. Replace every angle-bracket placeholder in
both copied templates and create `<deployment-repository>/fkst.lock` using the
same source IDs used by the declaration.

Before first use, the owner must:

1. Pin this mechanism's Git URL, full revision, and canonical tree hash in the deployment-owned lock.
2. Record target, platform, and engine pins in that lock.
3. Confirm that the target and platform checkout values name the same checkout. The durable root must retain the current store unless the owner deliberately accepts abandoning in-flight state.
4. Ensure all checkout/package roots and provider entrypoints exist and the engine binary is executable.
5. Run the pinned validator before any operator action:

   ```sh
   <deployment-repository>/bootstrap/run.sh <deployment-repository>/deployment.toml --machine-profile <deployment-repository>/.fkst/machine-profile.toml --lock <deployment-repository>/fkst.lock status
   ```

Invoke the deployment through `bootstrap/run.sh`; do not use a globally installed operator. The bootstrap accepts the declaration path first and passes all remaining machine-resolution arguments unchanged to the pinned `fkst-ops` entry.
