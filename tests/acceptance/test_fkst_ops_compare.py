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
NEW_OPERATOR = ROOT / "ops" / "dogfood.sh"
OLD_OPERATOR = Path("/Users/auric/fkst-" + "packages/.claude/skills/dogfood-github-devloop/dogfood.sh")
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
            "case \"$1\" in\n"
            "  status) printf '[packages] STOPPED   (target fixture/packages)\\n' ;;\n"
            f"  *) printf '{output} action=%s pid=%s\\n' \"$1\" \"$$\" ;;\n"
            "esac\n",
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

    def test_status_equal_mutation_fails(self) -> None:
        mutator = self.fixture.make_entry("mutator", "same")
        source = mutator.read_text(encoding="utf-8").replace(
            'case "$1" in', 'touch "$FKST_RUNTIME_ROOT/mutated"\ncase "$1" in'
        )
        mutator.write_text(source, encoding="utf-8")
        self.fixture.old = mutator
        self.fixture.new = mutator
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        status_cells = [cell for cell in report["cells"] if cell["old"]["action"] == "status"]
        self.assertTrue(status_cells)
        self.assertTrue(all(not cell["passed"] for cell in status_cells))

    def test_status_must_have_parseable_required_fields(self) -> None:
        malformed = self.fixture.make_entry("malformed", "same")
        malformed.write_text(
            malformed.read_text(encoding="utf-8").replace(
                "[packages] STOPPED   (target fixture/packages)", "status unknown"
            ),
            encoding="utf-8",
        )
        self.fixture.old = malformed
        self.fixture.new = malformed
        self.fixture.write_manifest()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("unparseable status line", result.stderr)

    def test_matrix_uses_real_old_and_new_operator_entries(self) -> None:
        self.assertTrue(OLD_OPERATOR.is_file())
        self.assertTrue(NEW_OPERATOR.is_file())
        cells = self.fixture.cells()
        for cell in cells:
            cell["old_entry"] = [str(OLD_OPERATOR)]
            cell["new_entry"] = [str(NEW_OPERATOR)]
        self.assertEqual(
            {(tuple(cell["old_entry"]), tuple(cell["new_entry"])) for cell in cells},
            {((str(OLD_OPERATOR),), (str(NEW_OPERATOR),))},
        )

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
