#!/usr/bin/env python3
"""Deterministic acceptance fixtures for the separately invoked doctor sweep."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "doctor" / "doctor.sh"
FIXTURE_CLOCK = 1_800_000_000


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.iterdir()):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class DoctorFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.receipts = self.root / "receipts"
        self.receipts.mkdir()
        self.processes = self.root / "processes.tsv"
        self.processes.write_text("", encoding="utf-8")
        self.engine = self.root / "engine"
        self.engine.write_text("#!/bin/sh\necho fixture-observe-failure >&2\nexit 1\n", encoding="utf-8")
        self.engine.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "FKST_OPS_DOCTOR_NOW_EPOCH": str(FIXTURE_CLOCK),
                "FKST_OPS_DOCTOR_PROCESS_FIXTURE": str(self.processes),
                "FKST_OPS_DOCTOR_SELF_PGID": "9999",
                "FKST_OPS_ENGINE_BINARY": str(self.engine),
                "DEPLOYMENT_OPERATOR_RECEIPT_SWEEP_ROOT": str(self.receipts),
                "FKST_OPS_DOCTOR_TARGETS": "declared\t/fixture/declared\t/fixture/no-durable",
            }
        )

    def close(self) -> None:
        self.temp.cleanup()

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(DOCTOR)],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def seed_processes(self, rows: list[tuple[object, ...]]) -> None:
        self.processes.write_text(
            "".join("\t".join(map(str, row)) + "\n" for row in rows), encoding="utf-8"
        )


class DoctorAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = DoctorFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_stray_supervise_detection_is_observational_and_accounted(self) -> None:
        self.fixture.seed_processes(
            [
                (101, 101, 1, "engine", "00:30", "engine supervise --project-root /fixture/declared x", 1),
                (102, 102, 1, "engine", "00:30", "engine supervise --project-root /fixture/stray x", 1),
            ]
        )
        before = self.fixture.processes.read_bytes()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("STRAY pid 102 project-root /fixture/stray", result.stdout)
        self.assertNotIn("STRAY pid 101", result.stdout)
        self.assertIn("stray-supervise findings: 1", result.stdout)
        self.assertIn("doctor accounting: 1 findings, 0 failures", result.stdout)
        self.assertEqual(self.fixture.processes.read_bytes(), before)

    def test_guarded_reaper_reaps_only_over_budget_orphan(self) -> None:
        self.fixture.seed_processes(
            [
                (200, 200, 1, "zsh", "01:00:00", "test harness", 1),
                (201, 200, 200, "fkst-framework", "00:46:00", "fkst-framework test", 1),
                (300, 300, 900, "zsh", "01:00:00", "live test harness", 1),
                (301, 300, 300, "fkst-framework", "00:46:00", "fkst-framework test", 1),
            ]
        )
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reaped fkst-framework test pid 201 pgid 200", result.stdout)
        self.assertIn("guarded-skip fkst-framework test pid 301 group 300", result.stdout)
        rows = {line.split("\t")[0]: line.rstrip().split("\t")[6] for line in self.fixture.processes.read_text().splitlines()}
        self.assertEqual(rows["200"], "0")
        self.assertEqual(rows["201"], "0")
        self.assertEqual(rows["300"], "1")
        self.assertEqual(rows["301"], "1")
        self.assertIn("1 reaped, 1 guarded-skip", result.stdout)

    def test_stale_receipt_cleanup_uses_fixed_fixture_clock(self) -> None:
        expired = self.fixture.receipts / "fkst-github-proxy-expired.md"
        current = self.fixture.receipts / "fkst-github-devloop-dashboard-current.json"
        expired.write_text("expired bytes", encoding="utf-8")
        current.write_text("current bytes", encoding="utf-8")
        os.utime(expired, (FIXTURE_CLOCK - 6 * 3600 - 1,) * 2)
        os.utime(current, (FIXTURE_CLOCK - 6 * 3600,) * 2)
        before = tree_hash(self.fixture.receipts)
        result = self.fixture.run()
        after = tree_hash(self.fixture.receipts)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"swept {expired}", result.stdout)
        self.assertIn(f"preserved {current}", result.stdout)
        self.assertIn("stale-tmp-receipt sweep: 1 reaped, 1 preserved", result.stdout)
        self.assertFalse(expired.exists())
        self.assertEqual(current.read_text(encoding="utf-8"), "current bytes")
        self.assertNotEqual(before, after)

    def test_durable_observe_failure_is_named_and_fail_visible(self) -> None:
        durable = self.fixture.root / "durable-one"
        durable.mkdir()
        (durable / "delivery.redb").write_bytes(b"fixed durable bytes")
        self.fixture.env["FKST_OPS_DOCTOR_TARGETS"] = f"durable-one\t/fixture/declared\t{durable}"
        durable_before = tree_hash(durable)
        process_before = self.fixture.processes.read_bytes()
        receipt_before = tree_hash(self.fixture.receipts)
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("failure durable.durable-one: observe operation failed: fixture-observe-failure", result.stdout)
        self.assertIn("doctor accounting: 0 findings, 1 failures", result.stdout)
        self.assertEqual(tree_hash(durable), durable_before)
        self.assertEqual(self.fixture.processes.read_bytes(), process_before)
        self.assertEqual(tree_hash(self.fixture.receipts), receipt_before)


if __name__ == "__main__":
    unittest.main()
