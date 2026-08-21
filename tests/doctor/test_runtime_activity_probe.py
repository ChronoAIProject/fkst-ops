"""Fixtures for the runtime artifact activity probe."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from ops.runtime_activity import (
    ActivityFact,
    ActivityInstrumentFailure,
    ActivityScan,
    FileSystemActivityInstrument,
    probe_runtime_activity,
)


class FixtureInstrument:
    name = "fixture.runtime-facts"

    def __init__(self, scan: ActivityScan | None = None, failure: Exception | None = None):
        self.result = scan
        self.failure = failure

    def scan(self, runtime_root: str) -> ActivityScan:
        if self.failure is not None:
            raise self.failure
        assert self.result is not None
        return self.result


class RuntimeActivityProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name) / "declared-runtime"
        self.runtime.mkdir()
        self.deployment = {
            "id": "deployment-a",
            "target_identity": "team/project-a",
            "machine": {"runtime": str(self.runtime)},
        }
        self.now = 1_800_000_000_000_000_000
        self.since = self.now - 3_600_000_000_000

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_complete_recursive_scan_finds_all_glob_independent_artifacts(self) -> None:
        nested = self.runtime / "nested"
        nested.mkdir()
        for index in range(34):
            path = nested / f"artifact-{index}.diff"
            path.write_text("fixture\n", encoding="ascii")
            os.utime(path, ns=(self.now - index - 1, self.now - index - 1))
        result = probe_runtime_activity(
            self.deployment,
            self.since,
            FileSystemActivityInstrument(),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "present")
        self.assertEqual(result.as_dict()["matching_count"], 34)
        self.assertTrue(result.coverage["complete"])
        self.assertGreaterEqual(result.coverage["entries_examined"], 35)

    def test_valid_empty_is_absent_only_with_complete_coverage(self) -> None:
        scan = ActivityScan(str(self.runtime), (), 0)
        result = probe_runtime_activity(
            self.deployment, self.since, FixtureInstrument(scan), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "absent")
        self.assertEqual(result.as_dict()["matching_count"], 0)
        self.assertTrue(result.coverage["complete"])
        self.assertIsNone(result.failure)

    def test_instrument_failure_is_unknown_and_preserved(self) -> None:
        result = probe_runtime_activity(
            self.deployment,
            self.since,
            FixtureInstrument(failure=ActivityInstrumentFailure("fixture scan unavailable")),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "instrument_failure")
        self.assertIn("fixture scan unavailable", result.failure["message"])
        self.assertFalse(result.coverage["complete"])

    def test_wrong_identity_is_unknown(self) -> None:
        other = str(Path(self.temp.name) / "other-runtime")
        scan = ActivityScan(other, (), 0)
        result = probe_runtime_activity(
            self.deployment, self.since, FixtureInstrument(scan), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "identity_failure")
        self.assertEqual(result.failure["observed_root"], other)

    def test_invalid_or_future_window_is_unknown(self) -> None:
        scan = ActivityScan(str(self.runtime), (), 0)
        for bound in ("not-an-epoch", -1, self.now + 1):
            with self.subTest(bound=bound):
                result = probe_runtime_activity(
                    self.deployment, bound, FixtureInstrument(scan), now_epoch_ns=self.now
                )
                self.assertEqual(result.state, "unknown")
                self.assertEqual(result.failure["kind"], "invalid_time")
                self.assertFalse(result.coverage["time_conversion_validated"])

    def test_future_artifact_time_is_unknown(self) -> None:
        fact = ActivityFact(str(self.runtime / "future.diff"), self.now + 1)
        scan = ActivityScan(str(self.runtime), (fact,), 1)
        result = probe_runtime_activity(
            self.deployment, self.since, FixtureInstrument(scan), now_epoch_ns=self.now
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "invalid_time")


if __name__ == "__main__":
    unittest.main()
