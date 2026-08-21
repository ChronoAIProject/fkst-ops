"""Typed observation of the terminal status for one engine run directory."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import time
from typing import Callable, Protocol

from ops.probe_result import ProbeFailure, ProbeResult


STATUS_NAME = "status.json"
MAX_STATUS_BYTES = 1024 * 1024


class CompletionInstrumentFailure(Exception):
    """The producer status instrument could not produce a valid terminal fact."""


@dataclass(frozen=True)
class CompletionFact:
    run_root: str
    run_id: str
    terminal_status: str
    outcome: str
    carrier_exit: int | None
    evidence_mtime_epoch_ns: int
    result_ref: str
    completion_sentinel_ref: str


class CompletionInstrument(Protocol):
    name: str

    def inspect(self, run_root: str) -> CompletionFact | None:
        ...


def _regular_file(path: Path) -> tuple[bytes, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CompletionInstrumentFailure(f"terminal evidence is not a regular file: {path}")
        if metadata.st_size <= 0 or metadata.st_size > MAX_STATUS_BYTES:
            raise CompletionInstrumentFailure(
                f"terminal evidence size is outside the bounded contract: {path}"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(MAX_STATUS_BYTES + 1)
    except OSError as exc:
        raise CompletionInstrumentFailure(f"cannot read terminal evidence {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > MAX_STATUS_BYTES:
        raise CompletionInstrumentFailure(f"terminal evidence exceeds the bounded contract: {path}")
    return content, metadata.st_mtime_ns


class ProducerStatusInstrument:
    """Read the terminal projection written by the recorded run producer."""

    name = "producer.status-json.v1"

    def inspect(self, run_root: str) -> CompletionFact | None:
        root = Path(run_root)
        try:
            metadata = root.lstat()
        except OSError as exc:
            raise CompletionInstrumentFailure(f"cannot inspect run root {root}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CompletionInstrumentFailure(
                f"run root is not an exact non-symlink directory: {root}"
            )
        status_path = root / STATUS_NAME
        try:
            content, modified_ns = _regular_file(status_path)
        except FileNotFoundError:
            return None
        try:
            document = json.loads(content)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise CompletionInstrumentFailure(
                f"terminal evidence is not one valid JSON object: {status_path}: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise CompletionInstrumentFailure("terminal evidence top level is not an object")
        required = {
            "schema_version", "flight_id", "attempt", "status", "reason_code",
            "carrier_exit", "run_dir", "result_ref", "completion_sentinel_ref",
        }
        if not required.issubset(document):
            raise CompletionInstrumentFailure("terminal evidence is missing required producer fields")
        schema_version = document["schema_version"]
        flight_id = document["flight_id"]
        attempt = document["attempt"]
        terminal_status = document["status"]
        outcome = document["reason_code"]
        carrier_exit = document["carrier_exit"]
        observed_root = document["run_dir"]
        result_ref = document["result_ref"]
        sentinel_ref = document["completion_sentinel_ref"]
        if schema_version != 1 or type(schema_version) is not int:
            raise CompletionInstrumentFailure("terminal evidence has an unsupported schema version")
        if not isinstance(flight_id, str) or not flight_id or type(attempt) is not int or attempt <= 0:
            raise CompletionInstrumentFailure("terminal evidence has an invalid producer run identity")
        if terminal_status not in {"COMPLETE", "NOT_COMPLETE"}:
            raise CompletionInstrumentFailure("terminal evidence has an invalid terminal status")
        if not isinstance(outcome, str) or not outcome:
            raise CompletionInstrumentFailure("terminal evidence has no outcome")
        if carrier_exit is not None and type(carrier_exit) is not int:
            raise CompletionInstrumentFailure("terminal evidence has an invalid carrier exit")
        if terminal_status == "COMPLETE" and (outcome != "COMPLETE" or carrier_exit != 0):
            raise CompletionInstrumentFailure("successful terminal evidence is internally inconsistent")
        if terminal_status == "NOT_COMPLETE" and outcome == "COMPLETE":
            raise CompletionInstrumentFailure("failed terminal evidence is internally inconsistent")
        if not all(isinstance(value, str) and value for value in
                   (observed_root, result_ref, sentinel_ref)):
            raise CompletionInstrumentFailure("terminal evidence has invalid artifact references")
        return CompletionFact(
            run_root=observed_root,
            run_id=f"{flight_id}/attempt-{attempt}",
            terminal_status=terminal_status,
            outcome=outcome,
            carrier_exit=carrier_exit,
            evidence_mtime_epoch_ns=modified_ns,
            result_ref=result_ref,
            completion_sentinel_ref=sentinel_ref,
        )


def _failure_result(
    run_root: str | None,
    now_ns: int,
    instrument_name: str,
    failure: ProbeFailure,
    *,
    selector_validated: bool,
    time_validated: bool,
    fact: CompletionFact | None = None,
) -> ProbeResult:
    return ProbeResult(
        probe="engine_completion_state",
        state="unknown",
        identity={"run_root": run_root, "run_id": fact.run_id if fact else None},
        observations={
            "terminal_status": fact.terminal_status if fact else None,
            "outcome": fact.outcome if fact else None,
            "carrier_exit": fact.carrier_exit if fact else None,
        },
        time={
            "basis": "unix_epoch_ns",
            "now": now_ns,
            "terminal_evidence_written": fact.evidence_mtime_epoch_ns if fact else None,
            "clock": "time.time_ns",
        },
        provenance={
            "terminal_evidence": f"{run_root}/{STATUS_NAME}" if run_root else None,
            "completion_instrument": instrument_name,
        },
        coverage={
            "scope": "one exact engine run terminal projection",
            "complete": False,
            "truncated": False,
            "selector_validated": selector_validated,
            "time_conversion_validated": time_validated,
        },
        failure=failure.as_dict(),
    )


def probe_engine_completion(
    run_root: str,
    instrument: CompletionInstrument | None = None,
    now_epoch_ns: int | None = None,
    clock: Callable[[], int] = time.time_ns,
) -> ProbeResult:
    """Answer completion only from the producer's terminal status projection."""
    now_ns = clock() if now_epoch_ns is None else now_epoch_ns
    instrument = instrument or ProducerStatusInstrument()
    if type(now_ns) is not int or now_ns <= 0:
        failure = ProbeFailure(
            "invalid_time", "captured system time is not a positive epoch nanosecond", captured_now=now_ns
        )
        return _failure_result(
            None, now_ns, instrument.name, failure,
            selector_validated=False, time_validated=False,
        )
    if (
        not isinstance(run_root, str)
        or not run_root
        or not os.path.isabs(run_root)
        or os.path.normpath(run_root) != run_root
    ):
        failure = ProbeFailure(
            "selector_invalid",
            "run root must be an absolute normalized path",
            requested_run_root=run_root,
        )
        return _failure_result(
            None, now_ns, instrument.name, failure,
            selector_validated=False, time_validated=True,
        )
    try:
        fact = instrument.inspect(run_root)
    except (CompletionInstrumentFailure, OSError) as exc:
        failure = ProbeFailure("instrument_failure", str(exc), operation="terminal_status_query")
        return _failure_result(
            run_root, now_ns, instrument.name, failure,
            selector_validated=True, time_validated=True,
        )
    if fact is None:
        return ProbeResult(
            probe="engine_completion_state",
            state="absent",
            identity={"run_root": run_root, "run_id": None},
            observations={"terminal_status": None, "outcome": None, "carrier_exit": None},
            time={"basis": "unix_epoch_ns", "now": now_ns,
                  "terminal_evidence_written": None, "clock": "time.time_ns"},
            provenance={"terminal_evidence": f"{run_root}/{STATUS_NAME}",
                        "completion_instrument": instrument.name,
                        "query": "producer terminal status is absent"},
            coverage={"scope": "one exact engine run terminal projection", "complete": True,
                      "truncated": False, "selector_validated": True,
                      "time_conversion_validated": True},
        )
    expected_result = str(Path(run_root) / "result.json")
    expected_sentinel = str(Path(run_root) / "completion.sentinel")
    if (
        fact.run_root != run_root
        or fact.result_ref != expected_result
        or fact.completion_sentinel_ref != expected_sentinel
    ):
        failure = ProbeFailure(
            "identity_failure",
            "terminal evidence identifies a different run or artifact set",
            requested_run_root=run_root,
            observed_run_root=fact.run_root,
            observed_result_ref=fact.result_ref,
            observed_completion_sentinel_ref=fact.completion_sentinel_ref,
        )
        return _failure_result(
            run_root, now_ns, instrument.name, failure, fact=fact,
            selector_validated=False, time_validated=True,
        )
    if (
        type(fact.evidence_mtime_epoch_ns) is not int
        or not 0 < fact.evidence_mtime_epoch_ns <= now_ns
    ):
        failure = ProbeFailure(
            "invalid_time",
            "terminal evidence time is outside the captured observation interval",
            terminal_evidence_written=fact.evidence_mtime_epoch_ns,
            captured_now=now_ns,
        )
        return _failure_result(
            run_root, now_ns, instrument.name, failure, fact=fact,
            selector_validated=True, time_validated=False,
        )
    return ProbeResult(
        probe="engine_completion_state",
        state="present",
        identity={"run_root": run_root, "run_id": fact.run_id},
        observations={"terminal_status": fact.terminal_status, "outcome": fact.outcome,
                      "carrier_exit": fact.carrier_exit},
        time={"basis": "unix_epoch_ns", "now": now_ns,
              "terminal_evidence_written": fact.evidence_mtime_epoch_ns,
              "clock": "time.time_ns"},
        provenance={"terminal_evidence": f"{run_root}/{STATUS_NAME}",
                    "completion_instrument": instrument.name,
                    "query": "producer-authored terminal status projection"},
        coverage={"scope": "one exact engine run terminal projection", "complete": True,
                  "truncated": False, "selector_validated": True,
                  "time_conversion_validated": True},
    )
