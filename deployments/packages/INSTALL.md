# Deployment installation

This repository intentionally contains no concrete deployment declaration. From
the fkst-ops repository root, install the declaration template in the deployment repository:

```sh
cp .fkst/deployment.example.toml <deployment-repository>/deployment.toml
```

Replace every declaration placeholder and create `<deployment-repository>/fkst.lock` using the
same source IDs. Commit both. Do not author a machine profile: it is generated from declaration
parameters and host discovery.

Before first use, the owner must:

1. Pin this mechanism's Git URL, full revision, and canonical tree hash in the deployment-owned lock.
2. Record target, platform, and engine pins in that lock.
3. Confirm that the target and platform checkout values name the same checkout. The durable root must retain the current store unless the owner deliberately accepts abandoning in-flight state.
4. Ensure all checkout/package roots and provider entrypoints exist and the engine binary is executable.
5. Generate all derived artifacts. Generation runs the real validator before publishing the plist:

   ```sh
   <fkst-ops-checkout>/bin/fkst-regenerate <deployment-repository>
   ```

Invoke the deployment with the exact configuration-only owner form above. The entry finds the
deployment root by walking upward from the declaration to the deployment lockfile, verifies whether its physical
checkout matches the mechanism pin, and otherwise hydrates and re-executes the pinned `fkst-ops`
with the original arguments.

## Install the cadence timer

`watch/cadence_round.py` defines one round; launchd owns when it runs. Regeneration reads
`cadence_interval_seconds` from the declaration and combines it with discovered paths and
`watch/com.fkst.cadence.plist.template`. Validate and load the generated timer:

```sh
plutil -lint "$HOME/Library/LaunchAgents/com.fkst.cadence.plist" && launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.fkst.cadence.plist"
```

To replace an already loaded instance, first run
`launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.fkst.cadence.plist"` and then run
the install command again. Do not commit the generated plist or machine profile. Every executable
file needed by a round remains in the pinned `fkst-ops` checkout; only those two pure-data files live
outside a repository. The configured ledger and standard output/error logs are runtime records, not
code required by the round.
