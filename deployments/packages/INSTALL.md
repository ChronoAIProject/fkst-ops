# Deployment installation

This repository intentionally contains no concrete deployment declaration. From
the fkst-ops repository root, install only the configuration templates in the
deployment repository:

```sh
cp .fkst/deployment.example.toml <deployment-repository>/deployment.toml
cp schema/examples/machine-profile.example.toml <deployment-repository>/.fkst/machine-profile.toml
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
   <fkst-ops-checkout>/bin/fkst-ops --declaration <deployment-repository>/deployment.toml --machine-profile <deployment-repository>/.fkst/machine-profile.toml status
   ```

Invoke the deployment with the exact configuration-only owner form above. The entry finds the
deployment root by walking upward from the declaration to the deployment lockfile, verifies whether its physical
checkout matches the mechanism pin, and otherwise hydrates and re-executes the pinned `fkst-ops`
with the original arguments.
