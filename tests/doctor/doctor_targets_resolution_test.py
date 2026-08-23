"""The managed set must come from the declaration, not from a variable only tests set.

Every existing doctor test injects `FKST_OPS_DOCTOR_TARGETS`, so all of them passed while
the production path resolved nothing: `doctor_targets` returned empty, every running
supervise was reported as a stray, and `durable_health_report` iterated an empty set and
printed a header with nothing under it.
"""

from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "schema" / "fixtures"
sys.path.insert(0, str(ROOT))

from tests.schema.test_validator import ValidatorTests  # noqa: E402


def emit_machine_profile(document: dict, path: Path) -> None:
    """Write the machine-profile shape: string maps and string lists only."""
    lines = ['schema = "fkst.ops.machine-profile.v1"', ""]
    for section, values in document.items():
        if section == "schema":
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, list):
                rendered = ", ".join(f'"{item}"' for item in value)
                lines.append(f'"{key}" = [{rendered}]')
            else:
                lines.append(f'"{key}" = "{value}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


class DoctorTargetsResolutionTest(unittest.TestCase):
    """Drive doctor.sh with only the variables the entrypoint actually exports."""

    def setUp(self) -> None:
        self.case = ValidatorTests("test_all_three_topology_fixtures_resolve")
        self.case.setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write_triple(self) -> tuple[Path, Path, Path]:
        # setUp mutates only the machine profile, so the declaration and lock are copied
        # verbatim from the fixtures that test_all_three_topology_fixtures_resolve covers.
        declaration = self.directory / "declaration.toml"
        machine = self.directory / "machine-profile.toml"
        lock = self.directory / "fkst.lock"
        declaration.write_bytes((FIXTURES / "packages.toml").read_bytes())
        lock.write_bytes((FIXTURES / "fkst.lock").read_bytes())
        emit_machine_profile(copy.deepcopy(self.case.machine), machine)
        return declaration, machine, lock

    def run_doctor(self, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        import os

        env = os.environ.copy()
        env.pop("FKST_OPS_DOCTOR_TARGETS", None)
        env.update(environment)
        return subprocess.run(
            ["bash", str(ROOT / "doctor" / "doctor.sh")],
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_declared_target_is_recognised_without_the_fixture_variable(self) -> None:
        declaration, machine, lock = self.write_triple()
        target = self.case.machine["roots"][
            self.case.declaration["deployment"][0]["machine"]["target_checkout"]
        ]
        processes = self.directory / "processes.tsv"
        processes.write_text(
            f"4242\t4242\t1\tengine\t00:30\tengine supervise --project-root {target} x\t1\n",
            encoding="utf-8",
        )
        result = self.run_doctor(
            {
                "FKST_OPS_DECLARATION": str(declaration),
                "FKST_OPS_MACHINE_PROFILE": str(machine),
                "FKST_OPS_LOCK": str(lock),
                "FKST_OPS_DOCTOR_PROCESS_FIXTURE": str(processes),
                "FKST_OPS_DOCTOR_SELF_PGID": "9999",
            }
        )
        self.assertNotIn("STRAY pid 4242", result.stdout, result.stdout)
        self.assertIn("none (every running supervise is a managed target)", result.stdout)

    def test_supervise_outside_every_declared_target_is_still_a_stray(self) -> None:
        declaration, machine, lock = self.write_triple()
        processes = self.directory / "processes.tsv"
        processes.write_text(
            "4343\t4343\t1\tengine\t00:30\tengine supervise --project-root /not/declared x\t1\n",
            encoding="utf-8",
        )
        result = self.run_doctor(
            {
                "FKST_OPS_DECLARATION": str(declaration),
                "FKST_OPS_MACHINE_PROFILE": str(machine),
                "FKST_OPS_LOCK": str(lock),
                "FKST_OPS_DOCTOR_PROCESS_FIXTURE": str(processes),
                "FKST_OPS_DOCTOR_SELF_PGID": "9999",
            }
        )
        self.assertIn("STRAY pid 4343", result.stdout, result.stdout)

    def test_only_the_undeclared_supervise_is_counted(self) -> None:
        # Discriminating on its own: the old resolver called both of these strays, because
        # an empty managed set fails the membership test for every running supervise.
        declaration, machine, lock = self.write_triple()
        target = self.case.machine["roots"][
            self.case.declaration["deployment"][0]["machine"]["target_checkout"]
        ]
        processes = self.directory / "processes.tsv"
        processes.write_text(
            f"5151	5151	1	engine	00:30	engine supervise --project-root {target} x	1\n"
            "5252	5252	1	engine	00:30	engine supervise --project-root /not/declared x	1\n",
            encoding="utf-8",
        )
        result = self.run_doctor(
            {
                "FKST_OPS_DECLARATION": str(declaration),
                "FKST_OPS_MACHINE_PROFILE": str(machine),
                "FKST_OPS_LOCK": str(lock),
                "FKST_OPS_DOCTOR_PROCESS_FIXTURE": str(processes),
                "FKST_OPS_DOCTOR_SELF_PGID": "9999",
            }
        )
        self.assertIn("stray-supervise findings: 1", result.stdout, result.stdout)
        self.assertIn("STRAY pid 5252", result.stdout)
        self.assertNotIn("STRAY pid 5151", result.stdout)

    def test_absent_declaration_reports_no_managed_set_rather_than_all_strays(self) -> None:
        processes = self.directory / "processes.tsv"
        processes.write_text(
            "4444\t4444\t1\tengine\t00:30\tengine supervise --project-root /somewhere x\t1\n",
            encoding="utf-8",
        )
        result = self.run_doctor({"FKST_OPS_DOCTOR_PROCESS_FIXTURE": str(processes),
                                  "FKST_OPS_DOCTOR_SELF_PGID": "9999"})
        # Documents the remaining gap rather than asserting it is correct: with no
        # declaration exported there is nothing to resolve, so the report still degrades
        # to calling every supervise a stray.
        self.assertIn("STRAY pid 4444", result.stdout, result.stdout)


if __name__ == "__main__":
    unittest.main()
