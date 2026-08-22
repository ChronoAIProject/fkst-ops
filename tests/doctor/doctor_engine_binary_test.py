"""The durable report must resolve its engine binary, not require an unexported variable.

`durable_health_one` demanded `FKST_OPS_ENGINE_BINARY` with `:?`. `host/bin_bootstrap.sh`
exports it; `bin/fkst-ops` does not, and the only other writers are two tests. So on the
doctor path the report either printed a header with nothing under it — while the managed set
was empty — or, once the managed set resolved, failed on an unbound variable. Neither state
reported durable health.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "doctor" / "targets.py"


def emit(resolved: dict) -> list[list[str]]:
    completed = subprocess.run(
        [sys.executable, str(TARGETS)], input=json.dumps(resolved),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return [line.split("\t") for line in completed.stdout.splitlines() if line]


def deployment(root: Path, *, revision_spec: object, platform: str = "target") -> dict:
    """A declared deployment whose platform directory is named by `platform`.

    Passing a name other than "target" reproduces the shape of a deployment whose target is the
    engine repository: the two checkouts are distinct directories, and only the platform carries
    the revision file, because an engine does not pin itself.
    """
    return {
        "id": "declared",
        "engine_revision": revision_spec,
        "machine": {
            "target_checkout": str(root / "target"),
            "platform_checkout": str(root / platform),
            "durable": str(root / "durable"),
            "logs": str(root / "logs"),
            "engine_binary": str(root / "bin" / "engine"),
        },
    }


def commit_revision_file(checkout: Path, revision: str) -> None:
    (checkout / ".fkst").mkdir(parents=True)
    (checkout / ".fkst" / "substrate-ref").write_text(revision + "\n", encoding="ascii")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    for name, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(checkout), "config", name, value], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True)


class DoctorEngineBinaryTest(unittest.TestCase):
    def test_the_row_carries_a_binary_resolved_from_the_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = "b" * 40
            commit_revision_file(root / "target", revision)

            rows = emit({"deployment": [deployment(root, revision_spec={"path": ".fkst/substrate-ref"})]})

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 5, rows)
        self.assertTrue(rows[0][4].endswith(f"engine-{revision}"), rows[0][4])

    def test_the_revision_comes_from_the_platform_when_it_is_not_the_target(self) -> None:
        """The shape of a deployment whose target is the engine repository.

        The operator derives `REVISION_SOURCE` from `platform_checkout`, so doctor must observe
        with the binary that derivation names. Deriving from the target instead yields an empty
        engine column here, and the durable report then reports the target as unresolvable while
        the engine it is running exists and is current.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = "c" * 40
            commit_revision_file(root / "platform", revision)
            (root / "target").mkdir()
            subprocess.run(["git", "init", "-q", str(root / "target")], check=True)

            rows = emit({"deployment": [
                deployment(root, revision_spec={"path": ".fkst/substrate-ref"}, platform="platform")
            ]})

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 5, rows)
        self.assertTrue(rows[0][4].endswith(f"engine-{revision}"), rows[0][4])

    def test_a_deployment_without_a_derivation_spec_yields_an_empty_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = emit({"deployment": [deployment(Path(directory), revision_spec=None)]})
        self.assertEqual(rows[0][4], "")

    def test_an_undherivable_revision_yields_an_empty_binary_rather_than_failing(self) -> None:
        # The sweep must keep reporting its other sections; a target whose revision cannot be
        # derived is reported as a failure by doctor.sh, not by aborting this emitter.
        with tempfile.TemporaryDirectory() as directory:
            rows = emit({
                "deployment": [
                    deployment(Path(directory), revision_spec={"path": ".fkst/substrate-ref"})
                ]
            })
        self.assertEqual(rows[0][4], "")

    def test_malformed_input_is_not_fatal(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(TARGETS)], input="not json",
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
