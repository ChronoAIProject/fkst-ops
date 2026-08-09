# Provider ports

This directory contains the mechanism-owned provider executables. The binding,
published-surface, and transport contracts are owned by
[`SPEC.md`](../SPEC.md#provider-binding-and-transport); the executable path and
kind registry is `schema/provider_surface.py`.

`engine.py` updates a declared Git checkout with `git pull --ff-only`, executes
the resolved argv-only build command without a shell, and verifies the declared
engine binary contract.

`board_github_control.sh` retains the GitHub classifier and resolves its
package-owned workflow and lifecycle fact tools through
`input.platform_checkout`.

`board_engine_durable.py` retains the engine observation, cache, health,
anomaly, and durable rendering behavior. AVM scoring rows are producer-owned;
the provider renders producer-supplied `avm_scoreboard` rows and owns only the
revert/reopen evidence analysis that is absent from the package producer.
