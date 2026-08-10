# fkst-ops Extraction Design

**Status:** Historical design record, superseded by [SPEC.md](../../../SPEC.md)
**Date:** 2026-08-08
**First proposed adopter:** `packages`

This record explains why the operational mechanism was extracted from its
first host repository. It does not define current behavior or guarantees.

The extraction separated four concerns that had previously been interleaved:
generic operational mechanism, deployment-owned parameters and pins,
machine-discovered facts, and package-owned fact semantics. A deployment-owned
lock and declaration were chosen to point one way at a pinned mechanism, while
provider ports retained producer ownership of semantic interpretation.

The design selected a self-pinning entry so an available checkout could verify
the deployment's full revision and canonical tree hash, hydrate the requested
checkout, run validation, and hand over to the pinned entry. It selected one
generic board front end backed by separate producer planes, deterministic
fixtures for migration comparison, and an explicit cutover rather than a
permanent dual mode.

The original plan proposed lifting the existing six deployment actions and
keeping `doctor` as a separately invoked sweep. It also recorded migration
work: declaration and provider validation, extraction of the existing operator,
candidate comparison, first-adopter soak, and eventual removal of the legacy
entry. Those were implementation and adoption decisions at the time, not
claims that every step subsequently occurred.

Several observations were intentionally incomplete. In particular, live
production equivalence, soak results, board-view differencing, and AVM
equivalence were not established by the read-only design review. The recurring
out-of-band `SIGTERM` later discussed in the operator was likewise inferred,
not observed live. These items remain historical unknowns unless current,
independent evidence establishes them.

For current ownership, action semantics, topology, replacement behavior, and
explicit non-guarantees, read [SPEC.md](../../../SPEC.md).

⟦AI:FKST⟧
