# Working Instructions

Read [SPEC.md](SPEC.md) before changing this repository. It is the sole
normative owner of repository guarantees. Do not put architecture doctrine,
deployment parameters, pins, credentials, machine paths, live process facts,
or copies of engine guarantees in this file.

## Editing

- Keep source code, comments, identifiers, logs, tests, and outward-facing
  documentation in English.
- Use only the Python standard library for repository Python code unless the
  specification is deliberately revised.
- Keep the mechanism generic. Concrete deployment declarations and parameters
  belong in the deployment repository; machine facts belong in its uncommitted
  machine profile; producer semantics remain behind provider contracts.
- Treat behavior changes separately from refactoring. For bug fixes, establish
  a failing test that reaches the relevant production path before changing it.
- Preserve unrelated worktree changes. Do not use destructive git commands.
- Keep secrets and credentials out of source, fixtures, logs, and command-line
  output.
- Do not infer live behavior from comments or stale documents. Mark hypotheses
  unproven until direct evidence exists.

## Verification

Run the focused test for changed code, then run:

```sh
python3 -m pytest -q
python3 scan/zero_target_names.py \
  --name fkst-packages --name fkst-substrate --name fkst-website
```

Use deterministic fixtures for acceptance work. Do not substitute live-network
results for fixture coverage. Live deployment actions require explicit operator
scope and the pinned entry; tests and documentation work must not restart or
reconfigure running deployments.

When a guarantee changes, update `SPEC.md` and the executable evidence together.
Other documentation may describe navigation, invocation, or history but must
not restate the guarantee.

⟦AI:FKST⟧
