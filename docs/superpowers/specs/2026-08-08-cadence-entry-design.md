# Cadence Entry Design

**Status:** Historical design record, superseded by [SPEC.md](../../../SPEC.md)
**Date:** 2026-08-08

This record preserves the rationale for a proposed cadence entry. It does not
define current behavior, ownership, installation instructions, or guarantees.
`SPEC.md` is the sole normative owner and explicitly excludes scheduling,
launchd installation, and deployment discovery from this repository's
guarantees.

The design proposed that `watch/cadence_round.py` perform one round without
owning a scheduler. The round would discover deployment declarations below an
explicitly supplied deployment repository, invoke `bin/fkst-ops` with `sync`
and then `status`, and append one JSON Lines record per declaration. It proposed
continuing after an individual declaration failure while making the round fail.

The proposed interface accepted the deployment repository, machine profile,
ledger, and operator entry through arguments or corresponding `FKST_WATCH_*`
environment variables. It preserved the incoming environment and did not set
`FKST_GITHUB_WRITE`. The proposal placed cadence policy in deployment data,
required declarations in one deployment repository to agree on a positive
interval, discovered machine paths, and generated a LaunchAgent from a
committed template rather than an operator-authored plist.

The proposal also generated `.fkst/machine-profile.toml`, mapped logical roots
and binaries under a conventional machine base, derived authenticated identity
from one active GitHub CLI account, and retained managed bot sets and
integration branches as deployment parameters. Validation before artifact
publication and the distinction between user and app authentication were part
of the rationale.

Requiring an explicit deployment repository was intended to avoid guessing
among multiple repositories. The proposed derived artifacts were a machine
profile and LaunchAgent plist; the ledger and standard streams were treated as
runtime records rather than authored inputs. These statements describe the
historical proposal only. They do not authorize generation, installation,
loading, replacement, or reconfiguration of a current deployment.
