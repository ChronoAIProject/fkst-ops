"""Typed observation of one declared deployment's supervise process.

The deployment pidfile is the identity claim made by ``host/host_run.sh``.  It is
useful only when the kernel agrees that the claimed pid is still the exact
supervise invocation for the declared project root.  This module deliberately
does not turn process observation into a general process census.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import errno
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from typing import Callable, Literal, Protocol


ProbeState = Literal["present", "absent", "unknown"]


class ProcessGone(Exception):
    """The selected pid no longer exists."""


class InstrumentFailure(Exception):
    """The host process instrument could not produce a valid fact."""


@dataclass(frozen=True)
class ProcessFact:
    pid: int
    argv: tuple[str, ...]
    started_epoch_ns: int
    state: str


class ProcessInstrument(Protocol):
    name: str

    def find_exact(self, project_root: str) -> list[int]:
        ...

    def inspect(self, pid: int) -> ProcessFact:
        ...


class ProbeFailure(Exception):
    def __init__(self, kind: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "message": str(self), **self.details}


@dataclass(frozen=True)
class ProbeResult:
    state: ProbeState
    identity: dict[str, str | None]
    pid: int | None
    time: dict[str, object]
    provenance: dict[str, object]
    coverage: dict[str, object]
    failure: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "probe": "deployment_process",
            "state": self.state,
            "identity": self.identity,
            "pid": self.pid,
            "time": self.time,
            "provenance": self.provenance,
            "coverage": self.coverage,
            "failure": self.failure,
        }


def _identity(deployment: dict[str, object]) -> dict[str, str | None]:
    machine = deployment.get("machine")
    if not isinstance(machine, dict):
        raise ProbeFailure("selector_invalid", "resolved deployment has no machine table")
    deployment_id = deployment.get("id")
    target_identity = deployment.get("target_identity")
    project_root = machine.get("target_checkout")
    durable_root = machine.get("durable")
    if not all(isinstance(value, str) and value for value in
               (deployment_id, target_identity, project_root, durable_root)):
        raise ProbeFailure(
            "selector_invalid",
            "resolved deployment is missing an exact process identity",
        )
    return {
        "deployment": deployment_id,
        "target_identity": target_identity,
        "project_root": project_root,
        "durable_root": durable_root,
    }


def _failure_result(
    identity: dict[str, str | None],
    now_ns: int,
    instrument_name: str | None,
    failure: ProbeFailure,
    pid: int | None = None,
    started_ns: int | None = None,
) -> ProbeResult:
    return ProbeResult(
        state="unknown",
        identity=identity,
        pid=pid,
        time={
            "basis": "unix_epoch_ns",
            "now": now_ns,
            "process_started": started_ns,
            "clock": "time.time_ns",
        },
        provenance={
            "configuration": "schema.validator resolved declaration",
            "pid_claim": "machine.durable/.fkst-supervise.pid",
            "process_instrument": instrument_name,
        },
        coverage={
            "scope": "one declared deployment supervise process",
            "complete": False,
            "truncated": False,
            "selector_validated": failure.kind != "selector_invalid",
            "time_conversion_validated": False,
        },
        failure=failure.as_dict(),
    )


def _read_pidfile(path: Path) -> tuple[int | None, bytes | None]:
    try:
        with path.open("rb") as stream:
            content = stream.read(128)
            if stream.read(1):
                raise ProbeFailure(
                    "pidfile_parse",
                    f"pidfile is longer than the bounded identity record: {path}",
                    path=str(path),
                )
    except FileNotFoundError:
        return None, None
    except ProbeFailure:
        raise
    except OSError as exc:
        raise ProbeFailure(
            "pidfile_query",
            f"cannot read pidfile {path}: {exc}",
            path=str(path),
            errno=getattr(exc, "errno", None),
        ) from exc
    if not content.endswith(b"\n"):
        raise ProbeFailure("pidfile_parse", f"pidfile has no terminating newline: {path}", path=str(path))
    value = content[:-1]
    if not value or not value.isdigit() or value.startswith(b"0"):
        raise ProbeFailure(
            "pidfile_parse",
            f"pidfile does not contain one positive decimal pid: {path}",
            path=str(path),
            observed=value.decode("ascii", "replace"),
        )
    return int(value), content


def _argv_selector(argv: tuple[str, ...], identity: dict[str, str | None]) -> bool:
    """Match argv as tokens; no substring or pipeline matching is permitted."""
    supervise_positions = [index for index, value in enumerate(argv) if value == "supervise"]
    if len(supervise_positions) != 1:
        return False
    position = supervise_positions[0]
    expected = ("--project-root", identity["project_root"])
    occurrences = [
        index for index in range(position + 1, len(argv) - 1)
        if argv[index:index + 2] == expected
    ]
    return len(occurrences) == 1


def _instrument_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "darwin.libproc+sysctl"
    if system == "Linux":
        return "linux.procfs"
    return f"{system.lower()}.unsupported"


class KernelProcessInstrument:
    """Read exact argv and kernel start facts for one pid."""

    name = _instrument_name()

    def inspect(self, pid: int) -> ProcessFact:
        system = platform.system()
        if system == "Darwin":
            return self._darwin(pid)
        if system == "Linux":
            return self._linux(pid)
        raise InstrumentFailure(f"no process instrument for {system}")

    @staticmethod
    def find_exact(project_root: str) -> list[int]:
        # pgrep is used only as an exact candidate selector.  The kernel argv fact
        # below remains authoritative, so a textual near-match cannot become present.
        pattern = r"(^|[[:space:]])supervise[[:space:]]+--project-root[[:space:]]+" + re.escape(project_root) + r"([[:space:]]|$)"
        try:
            completed = subprocess.run(
                ["pgrep", "-f", "--", pattern],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise InstrumentFailure(f"exact process selector could not start: {exc}") from exc
        if completed.returncode == 1:
            return []
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()[0] if completed.stderr.strip() else "no diagnostic"
            raise InstrumentFailure(f"exact process selector failed ({completed.returncode}): {detail}")
        pids: list[int] = []
        for line in completed.stdout.splitlines():
            if not re.fullmatch(r"[1-9][0-9]*", line):
                raise InstrumentFailure(f"exact process selector returned malformed pid: {line!r}")
            pids.append(int(line))
        return pids

    @staticmethod
    def _darwin(pid: int) -> ProcessFact:
        class BSDInfo(ctypes.Structure):
            _fields_ = [
                ("flags", ctypes.c_uint32), ("status", ctypes.c_uint32),
                ("xstatus", ctypes.c_uint32), ("pid", ctypes.c_uint32),
                ("ppid", ctypes.c_uint32), ("uid", ctypes.c_uint32),
                ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32),
                ("rgid", ctypes.c_uint32), ("svuid", ctypes.c_uint32),
                ("svgid", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
                ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
                ("nfiles", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
                ("pjobc", ctypes.c_uint32), ("tdev", ctypes.c_uint32),
                ("tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
                ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64),
            ]

        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidinfo = libproc.proc_pidinfo
            proc_pidinfo.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
            ]
            proc_pidinfo.restype = ctypes.c_int
            info = BSDInfo()
            received = proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
            if received != ctypes.sizeof(info):
                error = ctypes.get_errno()
                if error == errno.ESRCH:
                    raise ProcessGone
                raise InstrumentFailure(
                    f"proc_pidinfo returned {received} bytes for pid {pid} (errno {error})"
                )
            started_ns = info.start_sec * 1_000_000_000 + info.start_usec * 1_000
            if info.status == 5:  # SZOMB
                return ProcessFact(pid, (), started_ns, "Z")
            argv = _darwin_argv(pid)
            return ProcessFact(pid, argv, started_ns, str(info.status))
        except (ProcessGone, InstrumentFailure):
            raise
        except OSError as exc:
            raise InstrumentFailure(f"darwin process query failed for pid {pid}: {exc}") from exc

    @staticmethod
    def _linux(pid: int) -> ProcessFact:
        proc = Path("/proc") / str(pid)
        try:
            raw_stat = (proc / "stat").read_text(encoding="ascii")
            raw_cmdline = (proc / "cmdline").read_bytes()
            boot_line = next(
                line for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
                if line.startswith("btime ")
            )
            boot_epoch = int(boot_line.split()[1])
        except FileNotFoundError as exc:
            raise ProcessGone from exc
        except (OSError, StopIteration, ValueError) as exc:
            raise InstrumentFailure(f"linux process query failed for pid {pid}: {exc}") from exc
        closing = raw_stat.rfind(")")
        if closing < 0:
            raise InstrumentFailure(f"linux process stat has no closing command delimiter for pid {pid}")
        fields = raw_stat[closing + 2:].split()
        if len(fields) <= 19:
            raise InstrumentFailure(f"linux process stat is truncated for pid {pid}")
        state = fields[0]
        try:
            start_ticks = int(fields[19])
            clock_ticks = os.sysconf("SC_CLK_TCK")
            if clock_ticks <= 0:
                raise ValueError("SC_CLK_TCK is not positive")
        except (OSError, ValueError) as exc:
            raise InstrumentFailure(f"linux process start time is invalid for pid {pid}: {exc}") from exc
        if state == "Z":
            argv: tuple[str, ...] = ()
        else:
            if not raw_cmdline or not raw_cmdline.endswith(b"\0"):
                raise InstrumentFailure(f"linux process argv is empty or unterminated for pid {pid}")
            argv = tuple(os.fsdecode(part) for part in raw_cmdline.rstrip(b"\0").split(b"\0"))
        started_ns = boot_epoch * 1_000_000_000 + (start_ticks * 1_000_000_000) // clock_ticks
        return ProcessFact(pid, argv, started_ns, state)


def _darwin_argv(pid: int) -> tuple[str, ...]:
    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t,
    ]
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    for _ in range(2):
        size = ctypes.c_size_t(0)
        if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            error = ctypes.get_errno()
            if error == errno.ESRCH:
                raise ProcessGone
            raise InstrumentFailure(f"KERN_PROCARGS2 size query failed for pid {pid} (errno {error})")
        if size.value < 4 or size.value > 16 * 1024 * 1024:
            raise InstrumentFailure(f"KERN_PROCARGS2 returned invalid size for pid {pid}: {size.value}")
        buffer = ctypes.create_string_buffer(size.value)
        if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) == 0:
            raw = buffer.raw[:size.value]
            argc = int.from_bytes(raw[:4], byteorder=sys.byteorder, signed=True)
            if argc <= 0 or argc > 4096:
                raise InstrumentFailure(f"KERN_PROCARGS2 returned invalid argc for pid {pid}: {argc}")
            end = raw.find(b"\0", 4)
            if end < 0:
                raise InstrumentFailure(f"KERN_PROCARGS2 has no executable terminator for pid {pid}")
            position = end
            while position < len(raw) and raw[position] == 0:
                position += 1
            args: list[str] = []
            for _ in range(argc):
                end = raw.find(b"\0", position)
                if end < 0:
                    raise InstrumentFailure(f"KERN_PROCARGS2 argv is truncated for pid {pid}")
                args.append(os.fsdecode(raw[position:end]))
                position = end + 1
            return tuple(args)
        error = ctypes.get_errno()
        if error == errno.ESRCH:
            raise ProcessGone
        if error != errno.ENOMEM:
            raise InstrumentFailure(f"KERN_PROCARGS2 query failed for pid {pid} (errno {error})")
    raise InstrumentFailure(f"KERN_PROCARGS2 changed size repeatedly for pid {pid}")


def probe_deployment_process(
    deployment: dict[str, object],
    instrument: ProcessInstrument | None = None,
    now_epoch_ns: int | None = None,
    clock: Callable[[], int] = time.time_ns,
) -> ProbeResult:
    """Return a complete typed result for one resolved deployment."""
    now_ns = clock() if now_epoch_ns is None else now_epoch_ns
    instrument = instrument or KernelProcessInstrument()
    try:
        identity = _identity(deployment)
    except ProbeFailure as failure:
        return _failure_result({"deployment": deployment.get("id") if isinstance(deployment.get("id"), str) else None,
                                "target_identity": None, "project_root": None, "durable_root": None},
                               now_ns, instrument.name, failure)

    pid_file = Path(identity["durable_root"] or "") / ".fkst-supervise.pid"
    try:
        pid, original = _read_pidfile(pid_file)
    except ProbeFailure as failure:
        return _failure_result(identity, now_ns, instrument.name, failure)
    base_provenance = {
        "configuration": "schema.validator resolved declaration",
        "pid_claim": str(pid_file),
        "process_instrument": instrument.name,
    }
    base_coverage = {
        "scope": "one declared deployment supervise process",
        "complete": True,
        "truncated": False,
        "selector_validated": True,
        "time_conversion_validated": True,
    }
    if pid is None:
        try:
            candidates = instrument.find_exact(identity["project_root"] or "")
        except (AttributeError, InstrumentFailure) as exc:
            failure = ProbeFailure("instrument_failure", str(exc), operation="exact_selector")
            return _failure_result(identity, now_ns, instrument.name, failure)
        if not candidates:
            return ProbeResult(
                "absent", identity, None,
                {"basis": "unix_epoch_ns", "now": now_ns, "process_started": None, "clock": "time.time_ns"},
                {**base_provenance, "query": "pidfile absent; exact process selector returned empty"}, base_coverage,
            )
        selected: list[ProcessFact] = []
        for candidate in candidates:
            try:
                fact = instrument.inspect(candidate)
            except ProcessGone:
                continue
            except InstrumentFailure as exc:
                failure = ProbeFailure("instrument_failure", str(exc), operation="inspect", pid=candidate)
                return _failure_result(identity, now_ns, instrument.name, failure, pid=candidate)
            if fact.state == "Z":
                continue
            if fact.started_epoch_ns <= 0 or fact.started_epoch_ns > now_ns:
                failure = ProbeFailure(
                    "invalid_time",
                    "kernel process start time is outside the captured observation interval",
                    process_started=fact.started_epoch_ns,
                    captured_now=now_ns,
                    selected_pid=candidate,
                )
                return _failure_result(identity, now_ns, instrument.name, failure,
                                       pid=candidate, started_ns=fact.started_epoch_ns)
            if not _argv_selector(fact.argv, identity):
                failure = ProbeFailure(
                    "identity_failure",
                    "exact process candidate does not select the declared deployment",
                    selected_pid=candidate,
                    observed_argv=list(fact.argv),
                )
                return _failure_result(identity, now_ns, instrument.name, failure,
                                       pid=candidate, started_ns=fact.started_epoch_ns)
            selected.append(fact)
        if not selected:
            return ProbeResult(
                "absent", identity, None,
                {"basis": "unix_epoch_ns", "now": now_ns, "process_started": None, "clock": "time.time_ns"},
                {**base_provenance, "query": "exact candidates exited before kernel inspection"}, base_coverage,
            )
        if len(selected) != 1:
            failure = ProbeFailure(
                "identity_failure",
                "exact selector resolved more than one supervise process",
                selected_pids=[fact.pid for fact in selected],
            )
            return _failure_result(identity, now_ns, instrument.name, failure)
        fact = selected[0]
        return ProbeResult(
            "present", identity, fact.pid,
            {"basis": "unix_epoch_ns", "now": now_ns, "process_started": fact.started_epoch_ns, "clock": "time.time_ns"},
            {**base_provenance, "query": "exact process selector plus exact kernel argv and start time"}, base_coverage,
        )
    try:
        fact = instrument.inspect(pid)
    except ProcessGone:
        return ProbeResult(
            "absent", identity, None,
            {"basis": "unix_epoch_ns", "now": now_ns, "process_started": None, "clock": "time.time_ns"},
            {**base_provenance, "query": "pidfile selected pid; kernel reports it absent"}, base_coverage,
        )
    except InstrumentFailure as exc:
        failure = ProbeFailure("instrument_failure", str(exc), operation="inspect", pid=pid)
        return _failure_result(identity, now_ns, instrument.name, failure, pid=pid)
    if fact.pid != pid:
        failure = ProbeFailure("identity_failure", "instrument returned a different pid", selected_pid=pid,
                               observed_pid=fact.pid)
        return _failure_result(identity, now_ns, instrument.name, failure, pid=pid, started_ns=fact.started_epoch_ns)
    if fact.state == "Z":
        return ProbeResult(
            "absent", identity, None,
            {"basis": "unix_epoch_ns", "now": now_ns, "process_started": fact.started_epoch_ns, "clock": "time.time_ns"},
            {**base_provenance, "query": "pidfile selected a zombie process"}, base_coverage,
        )
    if fact.started_epoch_ns <= 0 or fact.started_epoch_ns > now_ns:
        failure = ProbeFailure(
            "invalid_time",
            "kernel process start time is outside the captured observation interval",
            process_started=fact.started_epoch_ns,
            captured_now=now_ns,
        )
        return _failure_result(identity, now_ns, instrument.name, failure, pid=pid, started_ns=fact.started_epoch_ns)
    if not _argv_selector(fact.argv, identity):
        failure = ProbeFailure(
            "identity_failure",
            "pidfile-selected process argv does not exactly select the declared deployment",
            selected_pid=pid,
            observed_argv=list(fact.argv),
        )
        return _failure_result(identity, now_ns, instrument.name, failure, pid=pid, started_ns=fact.started_epoch_ns)
    try:
        current, current_bytes = _read_pidfile(pid_file)
    except ProbeFailure as failure:
        return _failure_result(identity, now_ns, instrument.name, failure, pid=pid, started_ns=fact.started_epoch_ns)
    if current != pid or current_bytes != original:
        failure = ProbeFailure(
            "identity_failure",
            "pidfile changed while the process fact was being captured",
            selected_pid=pid,
            current_pid=current,
        )
        return _failure_result(identity, now_ns, instrument.name, failure, pid=pid, started_ns=fact.started_epoch_ns)
    return ProbeResult(
        "present", identity, pid,
        {"basis": "unix_epoch_ns", "now": now_ns, "process_started": fact.started_epoch_ns, "clock": "time.time_ns"},
        {**base_provenance, "query": "pidfile-selected pid plus exact kernel argv and start time"}, base_coverage,
    )
