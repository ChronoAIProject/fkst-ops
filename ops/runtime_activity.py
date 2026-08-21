"""Typed observation of artifact writes below one deployment runtime root."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import time
from typing import Callable, Protocol

from ops.probe_result import ProbeFailure, ProbeResult


class ActivityInstrumentFailure(Exception):
    """The filesystem instrument could not produce a complete fact."""


@dataclass(frozen=True)
class ActivityFact:
    path: str
    modified_epoch_ns: int


@dataclass(frozen=True)
class ActivityScan:
    root: str
    artifacts: tuple[ActivityFact, ...]
    entries_examined: int
    complete: bool = True
    truncated: bool = False


class ActivityInstrument(Protocol):
    name: str

    def scan(self, runtime_root: str) -> ActivityScan:
        ...


class FileSystemActivityInstrument:
    name = "python.os.scandir+stat"

    def scan(self, runtime_root: str) -> ActivityScan:
        root = Path(runtime_root)
        try:
            metadata = root.lstat()
        except OSError as exc:
            raise ActivityInstrumentFailure(
                f"cannot inspect runtime root {root}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ActivityInstrumentFailure(
                f"runtime root is not an exact non-symlink directory: {root}"
            )

        artifacts: list[ActivityFact] = []
        entries_examined = 0
        pending = [root]
        try:
            while pending:
                directory = pending.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        entry_metadata = entry.stat(follow_symlinks=False)
                        entries_examined += 1
                        if stat.S_ISDIR(entry_metadata.st_mode):
                            pending.append(Path(entry.path))
                        else:
                            artifacts.append(
                                ActivityFact(entry.path, entry_metadata.st_mtime_ns)
                            )
        except OSError as exc:
            raise ActivityInstrumentFailure(
                f"runtime tree scan failed below {root}: {exc}"
            ) from exc
        return ActivityScan(
            str(root), tuple(artifacts), entries_examined, complete=True, truncated=False
        )


def _identity(deployment: dict[str, object]) -> dict[str, object]:
    machine = deployment.get("machine")
    deployment_id = deployment.get("id")
    target_identity = deployment.get("target_identity")
    if not isinstance(machine, dict):
        raise ProbeFailure("selector_invalid", "resolved deployment has no machine table")
    runtime_root = machine.get("runtime")
    if not all(
        isinstance(value, str) and value
        for value in (deployment_id, target_identity, runtime_root)
    ):
        raise ProbeFailure(
            "selector_invalid",
            "resolved deployment is missing an exact runtime identity",
        )
    if not os.path.isabs(runtime_root) or os.path.normpath(runtime_root) != runtime_root:
        raise ProbeFailure(
            "selector_invalid",
            "resolved runtime root is not an absolute normalized path",
            runtime_root=runtime_root,
        )
    return {
        "deployment": deployment_id,
        "target_identity": target_identity,
        "runtime_root": runtime_root,
    }


def _parse_bound(value: str | int) -> int:
    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        parsed = int(value)
    else:
        raise ProbeFailure(
            "invalid_time",
            "window start must be a non-negative unix epoch nanosecond integer",
            supplied_bound=value,
        )
    if parsed < 0:
        raise ProbeFailure(
            "invalid_time",
            "window start must not be before the unix epoch",
            supplied_bound=value,
        )
    return parsed


def _failure_result(
    identity: dict[str, object],
    now_ns: int,
    since_ns: int | None,
    instrument_name: str,
    failure: ProbeFailure,
    *,
    selector_validated: bool,
    time_validated: bool,
) -> ProbeResult:
    return ProbeResult(
        probe="runtime_artifact_activity",
        state="unknown",
        identity=identity,
        observations={"matching_count": 0, "latest_path": None, "latest_modified": None},
        time={
            "basis": "unix_epoch_ns",
            "now": now_ns,
            "window_start": since_ns,
            "clock": "time.time_ns",
        },
        provenance={
            "configuration": "schema.validator resolved declaration",
            "filesystem_instrument": instrument_name,
        },
        coverage={
            "scope": "all non-directory artifacts below one declared runtime root",
            "complete": False,
            "truncated": False,
            "entries_examined": 0,
            "selector_validated": selector_validated,
            "time_conversion_validated": time_validated,
        },
        failure=failure.as_dict(),
    )


def probe_runtime_activity(
    deployment: dict[str, object],
    since_epoch_ns: str | int,
    instrument: ActivityInstrument | None = None,
    now_epoch_ns: int | None = None,
    clock: Callable[[], int] = time.time_ns,
) -> ProbeResult:
    """Answer whether an artifact was written in the inclusive epoch window."""
    now_ns = clock() if now_epoch_ns is None else now_epoch_ns
    instrument = instrument or FileSystemActivityInstrument()
    empty_identity = {
        "deployment": deployment.get("id") if isinstance(deployment.get("id"), str) else None,
        "target_identity": None,
        "runtime_root": None,
    }
    if type(now_ns) is not int or now_ns <= 0:
        failure = ProbeFailure(
            "invalid_time", "captured system time is not a positive epoch nanosecond", captured_now=now_ns
        )
        return _failure_result(
            empty_identity, now_ns, None, instrument.name, failure,
            selector_validated=False, time_validated=False,
        )
    try:
        identity = _identity(deployment)
    except ProbeFailure as failure:
        return _failure_result(
            empty_identity, now_ns, None, instrument.name, failure,
            selector_validated=False, time_validated=False,
        )
    try:
        since_ns = _parse_bound(since_epoch_ns)
    except ProbeFailure as failure:
        return _failure_result(
            identity, now_ns, None, instrument.name, failure,
            selector_validated=True, time_validated=False,
        )
    if since_ns > now_ns:
        failure = ProbeFailure(
            "invalid_time",
            "window start is after the captured system time",
            window_start=since_ns,
            captured_now=now_ns,
        )
        return _failure_result(
            identity, now_ns, since_ns, instrument.name, failure,
            selector_validated=True, time_validated=False,
        )

    runtime_root = str(identity["runtime_root"])
    try:
        scan = instrument.scan(runtime_root)
    except ActivityInstrumentFailure as exc:
        failure = ProbeFailure("instrument_failure", str(exc), operation="recursive_scan")
        return _failure_result(
            identity, now_ns, since_ns, instrument.name, failure,
            selector_validated=True, time_validated=True,
        )
    if scan.root != runtime_root:
        failure = ProbeFailure(
            "identity_failure",
            "filesystem instrument answered for a different runtime root",
            requested_root=runtime_root,
            observed_root=scan.root,
        )
        return _failure_result(
            identity, now_ns, since_ns, instrument.name, failure,
            selector_validated=False, time_validated=True,
        )
    if not scan.complete or scan.truncated:
        failure = ProbeFailure(
            "instrument_failure",
            "filesystem instrument returned partial coverage",
            complete=scan.complete,
            truncated=scan.truncated,
            entries_examined=scan.entries_examined,
        )
        return _failure_result(
            identity, now_ns, since_ns, instrument.name, failure,
            selector_validated=True, time_validated=True,
        )

    matching: list[ActivityFact] = []
    root_path = Path(runtime_root)
    for fact in scan.artifacts:
        fact_path = Path(fact.path)
        try:
            relative = fact_path.relative_to(root_path)
        except ValueError:
            relative = None
        if (
            not fact_path.is_absolute()
            or os.path.normpath(fact.path) != fact.path
            or relative is None
            or relative == Path(".")
        ):
            failure = ProbeFailure(
                "identity_failure",
                "filesystem instrument returned an artifact outside the runtime root",
                observed_path=fact.path,
                runtime_root=runtime_root,
            )
            return _failure_result(
                identity, now_ns, since_ns, instrument.name, failure,
                selector_validated=False, time_validated=True,
            )
        if type(fact.modified_epoch_ns) is not int or not 0 < fact.modified_epoch_ns <= now_ns:
            failure = ProbeFailure(
                "invalid_time",
                "artifact modification time is outside the captured observation interval",
                observed_path=fact.path,
                modified=fact.modified_epoch_ns,
                captured_now=now_ns,
            )
            return _failure_result(
                identity, now_ns, since_ns, instrument.name, failure,
                selector_validated=True, time_validated=False,
            )
        if fact.modified_epoch_ns >= since_ns:
            matching.append(fact)

    latest = max(matching, key=lambda fact: (fact.modified_epoch_ns, fact.path), default=None)
    coverage = {
        "scope": "all non-directory artifacts below one declared runtime root",
        "complete": True,
        "truncated": False,
        "entries_examined": scan.entries_examined,
        "selector_validated": True,
        "time_conversion_validated": True,
    }
    return ProbeResult(
        probe="runtime_artifact_activity",
        state="present" if matching else "absent",
        identity=identity,
        observations={
            "matching_count": len(matching),
            "latest_path": latest.path if latest else None,
            "latest_modified": latest.modified_epoch_ns if latest else None,
        },
        time={
            "basis": "unix_epoch_ns",
            "now": now_ns,
            "window_start": since_ns,
            "clock": "time.time_ns",
        },
        provenance={
            "configuration": "schema.validator resolved declaration",
            "filesystem_instrument": instrument.name,
            "query": "complete recursive scandir with non-following stat mtimes",
        },
        coverage=coverage,
    )
