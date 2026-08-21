"""Fixtures for the deployment process and identity probe."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ops.deployment_process import (
    InstrumentFailure,
    ProcessFact,
    ProbeState,
    probe_deployment_process,
)


class FixtureInstrument:
    name = "fixture.process-facts"

    def __init__(self, fact: ProcessFact | None = None, failure: Exception | None = None):
        self.fact = fact
        self.failure = failure

    def find_exact(self, project_root: str) -> list[int]:
        return []

    def inspect(self, pid: int) -> ProcessFact:
        if self.failure is not None:
            raise self.failure
        assert self.fact is not None
        return self.fact


class DeploymentProcessProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "declared-project"
        self.durable = root / "declared-durable"
        self.project.mkdir()
        self.durable.mkdir()
        self.deployment = {
            "id": "deployment-a",
            "target_identity": "team/project-a",
            "machine": {
                "target_checkout": str(self.project),
                "durable": str(self.durable),
            },
        }
        self.now = 1_800_000_000_000_000_000

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_pid(self, pid: int) -> None:
        (self.durable / ".fkst-supervise.pid").write_text(f"{pid}\n", encoding="ascii")

    def fact(self, *, root: Path | None = None, started: int | None = None) -> ProcessFact:
        return ProcessFact(
            4101,
            ("/srv/engine", "supervise", "--project-root", str(root or self.project)),
            self.now - 1 if started is None else started,
            "2",
        )

    def assert_state(self, expected: ProbeState, result) -> None:
        self.assertEqual(result.state, expected)
        self.assertEqual(result.as_dict()["state"], expected)

    def test_present_returns_exact_identity_pid_and_epoch_basis(self) -> None:
        self.write_pid(4101)
        result = probe_deployment_process(
            self.deployment, FixtureInstrument(self.fact()), now_epoch_ns=self.now
        )
        self.assert_state("present", result)
        self.assertEqual(result.pid, 4101)
        self.assertEqual(result.identity["project_root"], str(self.project))
        self.assertEqual(result.time["now"], self.now)
        self.assertEqual(result.time["process_started"], self.now - 1)
        self.assertEqual(result.time["basis"], "unix_epoch_ns")
        self.assertTrue(result.coverage["complete"])
        self.assertIsNone(result.failure)

    def test_valid_empty_is_absent_only_with_complete_coverage(self) -> None:
        result = probe_deployment_process(
            self.deployment, FixtureInstrument(), now_epoch_ns=self.now
        )
        self.assert_state("absent", result)
        self.assertIsNone(result.pid)
        self.assertTrue(result.coverage["complete"])
        self.assertIsNone(result.failure)

    def test_instrument_failure_is_unknown_and_non_scalar(self) -> None:
        self.write_pid(4101)
        result = probe_deployment_process(
            self.deployment,
            FixtureInstrument(failure=InstrumentFailure("fixture instrument unavailable")),
            now_epoch_ns=self.now,
        )
        self.assert_state("unknown", result)
        self.assertEqual(result.failure["kind"], "instrument_failure")
        self.assertIn("fixture instrument unavailable", result.failure["message"])
        self.assertFalse(result.coverage["complete"])
        self.assertEqual(result.pid, 4101)

    def test_wrong_identity_is_unknown_instead_of_substring_present(self) -> None:
        self.write_pid(4101)
        other_root = self.project / "packages-other-deployment"
        result = probe_deployment_process(
            self.deployment,
            FixtureInstrument(self.fact(root=other_root)),
            now_epoch_ns=self.now,
        )
        self.assert_state("unknown", result)
        self.assertEqual(result.failure["kind"], "identity_failure")
        self.assertEqual(result.failure["selected_pid"], 4101)
        self.assertIn(str(other_root), result.failure["observed_argv"])

    def test_invalid_and_future_process_time_are_unknown(self) -> None:
        self.write_pid(4101)
        for bad_time in (0, self.now + 1):
            with self.subTest(started=bad_time):
                result = probe_deployment_process(
                    self.deployment,
                    FixtureInstrument(self.fact(started=bad_time)),
                    now_epoch_ns=self.now,
                )
                self.assert_state("unknown", result)
                self.assertEqual(result.failure["kind"], "invalid_time")
                self.assertEqual(result.failure["process_started"], bad_time)


if __name__ == "__main__":
    unittest.main()
