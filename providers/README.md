# Provider ports

This directory contains the mechanism-owned provider executables. The binding,
published-surface, and transport contracts are owned by
[`SPEC.md`](../SPEC.md#provider-binding-and-transport); the executable path and
kind registry is `schema/provider_surface.py`.

`engine.py` fetches the exact declared revision, detaches the engine checkout at
that commit, executes the resolved argv-only Cargo build command without a shell,
atomically points the declared binary at the selected package product, and
rejects any pre-build, post-build, product, result, binary, or receipt mismatch.

`board_github_control.sh` retains the GitHub classifier and resolves its
package-owned workflow and lifecycle fact tools through
`input.platform_checkout`.

`board_engine_durable.py` retains the engine observation, cache, health,
anomaly, and durable rendering behavior. AVM scoring rows are producer-owned;
the provider renders producer-supplied `avm_scoreboard` rows and owns only the
revert/reopen evidence analysis that is absent from the package producer.
