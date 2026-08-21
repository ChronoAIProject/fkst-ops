"""Fixtures for the engine completion state probe."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from ops.engine_completion import (
    CompletionFact,
    CompletionInstrumentFailure,
    ProducerStatusInstrument,
    probe_engine_completion,
)


class FixtureInstrument:
    name = "fixture.completion-facts"

    def __init__(self, fact: CompletionFact | None = None, failure: Exception | None = None):
        self.fact = fact
        self.failure = failure

    def inspect(self, run_root: str) -> CompletionFact | None:
        if self.failure is not None:
            raise self.failure
        return self.fact


class CompletionProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.run_root = Path(self.temp.name) / "attempt-1"
        self.run_root.mkdir()
        self.now = 1_800_000_000_000_000_000

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fact(
        self,
        *,
        root: Path | None = None,
        modified: int | None = None,
        status: str = "COMPLETE",
        outcome: str = "COMPLETE",
        carrier_exit: int | None = 0,
    ) -> CompletionFact:
        selected = root or self.run_root
        return CompletionFact(
            str(selected), "flight-a/attempt-1", status, outcome, carrier_exit,
            self.now - 1 if modified is None else modified,
            str(selected / "result.json"), str(selected / "completion.sentinel"),
        )

    def test_terminal_status_is_present_with_exact_outcome(self) -> None:
        result = probe_engine_completion(
            str(self.run_root), FixtureInstrument(self.fact()), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "present")
        self.assertEqual(result.identity["run_id"], "flight-a/attempt-1")
        self.assertEqual(result.as_dict()["terminal_status"], "COMPLETE")
        self.assertEqual(result.as_dict()["outcome"], "COMPLETE")
        self.assertTrue(result.coverage["complete"])

    def test_partial_result_artifact_without_terminal_status_is_absent(self) -> None:
        (self.run_root / "result.json").write_text('{"partial":true}\n', encoding="ascii")
        result = probe_engine_completion(
            str(self.run_root), ProducerStatusInstrument(), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "absent")
        self.assertIsNone(result.as_dict()["terminal_status"])
        self.assertTrue(result.coverage["complete"])

    def test_instrument_failure_is_unknown_and_preserved(self) -> None:
        result = probe_engine_completion(
            str(self.run_root),
            FixtureInstrument(failure=CompletionInstrumentFailure("fixture status unreadable")),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "instrument_failure")
        self.assertIn("fixture status unreadable", result.failure["message"])
        self.assertFalse(result.coverage["complete"])

    def test_wrong_identity_is_unknown(self) -> None:
        other = Path(self.temp.name) / "attempt-2"
        result = probe_engine_completion(
            str(self.run_root), FixtureInstrument(self.fact(root=other)), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "identity_failure")
        self.assertEqual(result.failure["observed_run_root"], str(other))

    def test_invalid_or_future_terminal_time_is_unknown(self) -> None:
        for modified in (0, self.now + 1):
            with self.subTest(modified=modified):
                result = probe_engine_completion(
                    str(self.run_root), FixtureInstrument(self.fact(modified=modified)),
                    now_epoch_ns=self.now,
                )
                self.assertEqual(result.state, "unknown")
                self.assertEqual(result.failure["kind"], "invalid_time")
                self.assertFalse(result.coverage["time_conversion_validated"])

    def test_failed_terminal_status_is_present_with_failure_outcome(self) -> None:
        fact = self.fact(status="NOT_COMPLETE", outcome="CARRIER_EXIT_NONZERO", carrier_exit=1)
        result = probe_engine_completion(
            str(self.run_root), FixtureInstrument(fact), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "present")
        self.assertEqual(result.as_dict()["terminal_status"], "NOT_COMPLETE")
        self.assertEqual(result.as_dict()["outcome"], "CARRIER_EXIT_NONZERO")

    def test_production_instrument_reads_terminal_projection(self) -> None:
        status = {
            "schema_version": 1,
            "flight_id": "flight-a",
            "attempt": 1,
            "status": "COMPLETE",
            "reason_code": "COMPLETE",
            "carrier_exit": 0,
            "run_dir": str(self.run_root),
            "result_ref": str(self.run_root / "result.json"),
            "completion_sentinel_ref": str(self.run_root / "completion.sentinel"),
        }
        path = self.run_root / "status.json"
        path.write_text(json.dumps(status), encoding="ascii")
        os.utime(path, ns=(self.now - 1, self.now - 1))
        result = probe_engine_completion(
            str(self.run_root), ProducerStatusInstrument(), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "present")
        self.assertEqual(result.identity["run_id"], "flight-a/attempt-1")


if __name__ == "__main__":
    unittest.main()
