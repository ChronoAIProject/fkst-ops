# Deployment installation

This repository intentionally contains no deployment declaration. Install the
generic files from `bootstrap/` in the deployment repository, then create the
declaration, lock, and machine profile there using the schema specification.

Before first use, the owner must:

1. Pin this mechanism's Git URL, full revision, and canonical tree hash in the deployment-owned lock.
2. Record target, platform, and engine pins in that lock.
3. Copy `.fkst/machine-profile.example.toml` to an untracked machine-local file and replace every angle-bracket placeholder. The target and platform checkout values must name the same checkout. The durable root must retain the current store unless the owner deliberately accepts abandoning in-flight state.
4. Ensure all checkout/package roots and provider entrypoints exist and the engine binary is executable.
5. Run the pinned validator before any operator action.

Invoke the deployment through `bootstrap/run.sh`; do not use a globally installed operator. The bootstrap accepts the declaration path first and passes all remaining machine-resolution arguments unchanged to the pinned `fkst-ops` entry.
