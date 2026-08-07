#!/usr/bin/env python3
"""Tests for the bounded five-action equivalence comparator."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
COMPARATOR = ROOT / "acceptance" / "fkst-ops-compare"
MATRIX = {
    "board": ("both-healthy", "engine-durable-failed", "github-control-failed", "both-failed"),
    "status": ("stopped", "running"),
    "logs": ("default-n", "explicit-n", "multiple-candidate-logs"),
    "restart": ("success", "failure-with-rollback"),
    "sync": ("current", "stale-package", "stale-engine", "dirty-worktree", "diverged-branch"),
}


class ComparatorFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.seed = self.root / "seed"
        for name in ("source", "runtime", "durable", "logs", "cache", "process"):
            path = self.seed / name
            path.mkdir(parents=True)
            (path / "seed.txt").write_text(f"{name}\n", encoding="utf-8")
        self.old = self.make_entry("old", "same")
        self.new = self.make_entry("new", "same")
        self.manifest = self.root / "fixture.json"
        self.output = self.root / "observations.json"
        self.write_manifest()

    def close(self) -> None:
        self.temporary.cleanup()

    def make_entry(self, name: str, output: str) -> Path:
        path = self.root / name
        path.write_text(
            "#!/bin/sh\n"
            ": \"${FKST_OPS_FIXTURE_MODE:?}\" \"${FKST_OPS_NETWORK_DISABLED:?}\"\n"
            ": \"${FKST_OPS_SOURCE_CHECKOUT:?}\" \"${FKST_RUNTIME_ROOT:?}\"\n"
            ": \"${FKST_DURABLE_ROOT:?}\" \"${FKST_LOG_DIR:?}\"\n"
            ": \"${FKST_CACHE_ROOT:?}\" \"${FKST_PROCESS_NAMESPACE:?}\"\n"
            f"printf '{output} action=%s pid=%s\\n' \"$1\" \"$$\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def cells(self) -> list[dict[str, object]]:
        return [
            {
                "fixture_id": fixture_id,
                "action": action,
                "argv": [],
                "old_entry": [str(self.old)],
                "new_entry": [str(self.new)],
                "provider_fixture_ids": [f"{action}-{fixture_id}"],
            }
            for action, fixture_ids in MATRIX.items()
            for fixture_id in fixture_ids
        ]

    def write_manifest(self, cells: list[dict[str, object]] | None = None) -> None:
        self.manifest.write_text(
            json.dumps({
                "version": "fkst.ops.compare.v1", "seed_root": "seed",
                "timeout_seconds": 5, "cells": self.cells() if cells is None else cells,
            }),
            encoding="utf-8",
        )

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(COMPARATOR), "--fixture", str(self.manifest), "--output", str(self.output)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )


class FkstOpsCompareTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = ComparatorFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_complete_equivalent_matrix_passes_and_records_closed_observations(self) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["cells"]), 16)
        record = report["cells"][0]["old"]
        required = {
            "fixture_id", "action", "argv", "provider_fixture_ids", "seed_refs", "exit_code",
            "stdout_normalized", "stderr_normalized", "git_before", "git_after",
            "runtime_before", "runtime_after", "durable_before", "durable_after",
            "process_state_before", "process_state_after",
        }
        self.assertEqual(set(record), required)
        self.assertIn("pid=<PID>", record["stdout_normalized"])

    def test_deliberately_divergent_pair_fails(self) -> None:
        self.fixture.new = self.fixture.make_entry("new", "different")
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertTrue(any(not cell["passed"] for cell in report["cells"]))

    def test_missing_per_side_isolation_is_setup_failure(self) -> None:
        missing = self.fixture.seed / "cache"
        for child in missing.iterdir():
            child.unlink()
        missing.rmdir()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("seed lacks required isolation resource: cache", result.stderr)

    def test_missing_required_cell_is_gate_failure(self) -> None:
        self.fixture.write_manifest(self.fixture.cells()[:-1])
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertEqual(report["missing_cells"], ["sync/diverged-branch"])


if __name__ == "__main__":
    unittest.main()
