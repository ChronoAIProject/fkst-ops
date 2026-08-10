# Deployment installation

**Status:** Historical installation record, superseded by [SPEC.md](../../SPEC.md)

This file preserves the former `packages` installation outline. It is not a
current runbook, contract, or authorization to install or reconfigure a
deployment. `SPEC.md` is the sole normative owner and explicitly excludes
scheduling, launchd installation, deployment discovery, concrete deployment
declarations, machine paths, credentials, pins, and pin advancement from this
repository's guarantees.

The former outline placed the concrete declaration and lock in a deployment
repository, kept machine facts outside version control, and required validation
before use. It proposed generating a machine profile and LaunchAgent from
deployment parameters and host discovery, then loading the generated cadence
timer with launchd. Those details are retained only as historical rationale;
they do not describe a supported installation procedure.

Specifically, the outline recorded that this repository contained "no concrete deployment declaration,"
that the deployment repository supplied the
"deployment-owned lock," and that generation used `bin/fkst-regenerate`. Its
machine-profile instruction was "Do not author a machine profile" because the
proposal generated that artifact. These are preserved design facts, not current
operator requirements.

For the current mechanism ownership, entry validation, operational action
surface, and explicit non-guarantees, read [SPEC.md](../../SPEC.md).
