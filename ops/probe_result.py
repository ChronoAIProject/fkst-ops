"""Shared typed result shape for the enumerated doctor probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ProbeState = Literal["present", "absent", "unknown"]


class ProbeFailure(Exception):
    def __init__(self, kind: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "message": str(self), **self.details}


@dataclass(frozen=True)
class ProbeResult:
    probe: str
    state: ProbeState
    identity: dict[str, object]
    observations: dict[str, object] = field(default_factory=dict)
    time: dict[str, object] = field(default_factory=dict)
    provenance: dict[str, object] = field(default_factory=dict)
    coverage: dict[str, object] = field(default_factory=dict)
    failure: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "probe": self.probe,
            "state": self.state,
            "identity": self.identity,
        }
        reserved = set(result) | {"time", "provenance", "coverage", "failure"}
        overlap = reserved.intersection(self.observations)
        if overlap:
            raise ValueError(f"probe observations collide with result field: {sorted(overlap)[0]}")
        result.update(self.observations)
        result.update({
            "time": self.time,
            "provenance": self.provenance,
            "coverage": self.coverage,
            "failure": self.failure,
        })
        return result
