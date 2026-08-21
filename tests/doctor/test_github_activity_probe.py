"""Fixtures for the exact GitHub entity activity probe."""

from __future__ import annotations

import unittest

from ops.github_activity import (
    GithubEntityFact,
    GithubInstrumentFailure,
    probe_github_activity,
)


class FixtureInstrument:
    name = "fixture.github-facts"

    def __init__(self, fact: GithubEntityFact | None = None, failure: Exception | None = None):
        self.fact = fact
        self.failure = failure

    def inspect(self, repository: str, entity_number: int) -> GithubEntityFact:
        if self.failure is not None:
            raise self.failure
        assert self.fact is not None
        return self.fact


class GithubActivityProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.deployment = {"id": "deployment-a", "target_identity": "team/project-a"}
        self.entity_number = 17
        self.now = 1_800_000_000_000_000_000
        self.since = "2027-01-01T00:00:00Z"

    def fact(
        self,
        *,
        repository: str = "team/project-a",
        number: int | None = None,
        updated: str = "2026-12-31T00:00:00Z",
    ) -> GithubEntityFact:
        selected_number = self.entity_number if number is None else number
        return GithubEntityFact(
            repository, selected_number, "NODE_exact_identity",
            f"https://github.com/{repository}/issues/{selected_number}",
            "issue", updated,
        )

    def test_recent_server_update_is_present_with_exact_identity(self) -> None:
        result = probe_github_activity(
            self.deployment,
            self.entity_number,
            self.since,
            FixtureInstrument(self.fact(updated="2027-01-02T00:00:00Z")),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "present")
        self.assertEqual(result.identity["repository"], "team/project-a")
        self.assertEqual(result.identity["entity_number"], self.entity_number)
        self.assertEqual(result.identity["entity_node_id"], "NODE_exact_identity")
        self.assertEqual(result.time["basis"], "unix_epoch_ns")
        self.assertTrue(result.coverage["complete"])

    def test_valid_empty_is_absent_only_with_complete_coverage(self) -> None:
        result = probe_github_activity(
            self.deployment, self.entity_number, self.since,
            FixtureInstrument(self.fact()), now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "absent")
        self.assertTrue(result.coverage["complete"])
        self.assertEqual(result.coverage["pages"], 1)
        self.assertIsNone(result.failure)

    def test_instrument_failure_is_unknown_and_preserved(self) -> None:
        result = probe_github_activity(
            self.deployment,
            self.entity_number,
            self.since,
            FixtureInstrument(failure=GithubInstrumentFailure("fixture API unavailable")),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "instrument_failure")
        self.assertIn("fixture API unavailable", result.failure["message"])
        self.assertFalse(result.coverage["complete"])

    def test_wrong_identity_is_unknown(self) -> None:
        result = probe_github_activity(
            self.deployment, self.entity_number, self.since,
            FixtureInstrument(self.fact(repository="team/project-a-nearby")),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "identity_failure")
        self.assertEqual(result.failure["observed_repository"], "team/project-a-nearby")

    def test_invalid_local_or_future_since_time_is_unknown(self) -> None:
        for since in ("2027-01-01T08:00:00+08:00", "2030-01-01T00:00:00Z"):
            with self.subTest(since=since):
                result = probe_github_activity(
                    self.deployment, self.entity_number, since,
                    FixtureInstrument(self.fact()), now_epoch_ns=self.now,
                )
                self.assertEqual(result.state, "unknown")
                self.assertEqual(result.failure["kind"], "invalid_time")
                self.assertFalse(result.coverage["time_conversion_validated"])

    def test_future_server_activity_time_is_unknown(self) -> None:
        result = probe_github_activity(
            self.deployment, self.entity_number, self.since,
            FixtureInstrument(self.fact(updated="2030-01-01T00:00:00Z")),
            now_epoch_ns=self.now,
        )
        self.assertEqual(result.state, "unknown")
        self.assertEqual(result.failure["kind"], "invalid_time")


if __name__ == "__main__":
    unittest.main()
