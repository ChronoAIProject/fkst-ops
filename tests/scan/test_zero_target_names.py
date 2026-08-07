import subprocess
import tempfile
import unittest
from pathlib import Path


SCAN = Path(__file__).resolve().parents[2] / "scan" / "zero_target_names.py"
FIXTURE = Path(__file__).parent / "fixtures" / "concrete-name.txt"
SCHEMA_FIXTURES = (
    "tests/schema/fixtures/fkst.lock",
    "tests/schema/fixtures/machine-profile.toml",
    "tests/schema/fixtures/packages.toml",
    "tests/schema/fixtures/substrate.toml",
    "tests/schema/fixtures/website.toml",
)
NAMES = (
    FIXTURE.read_text(encoding="utf-8").strip(),
    *("fkst-" + Path(relative).stem for relative in SCHEMA_FIXTURES[-3:]),
)


class ZeroTargetNamesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, text: str, tracked: bool = True):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if tracked:
            subprocess.run(["git", "-C", str(self.root), "add", relative], check=True)

    def invoke(self, *extra: str):
        names = [argument for name in NAMES for argument in ("--name", name)]
        return subprocess.run(
            ["python3", str(SCAN), "--root", str(self.root), *names, *extra],
            text=True, capture_output=True,
        )

    def test_positive_and_untracked_files_are_not_source(self):
        self.write("source.py", "generic source\n")
        self.write("scratch.txt", NAMES[0], tracked=False)
        self.assertEqual(0, self.invoke().returncode)

    def test_concrete_repository_name_anywhere_else_fails(self):
        for index, name in enumerate(NAMES):
            with self.subTest(name=name):
                relative = f"source-{index}.py"
                self.write(relative, name)
                result = self.invoke()
                self.assertEqual(1, result.returncode)
                self.assertIn(f"{relative}: concrete target name: {name}", result.stderr)

    def test_unenumerated_exclusion_is_scan_failure(self):
        self.write("source.py", NAMES[0])
        result = self.invoke("--exclude", "source.py")
        self.assertEqual(2, result.returncode)
        self.assertIn("unenumerated exclusion", result.stderr)

    def test_enumerated_fixtures_are_excluded(self):
        for index, relative in enumerate(SCHEMA_FIXTURES):
            self.write(relative, NAMES[index % len(NAMES)])
        self.write("tests/scan/fixtures/concrete-name.txt", NAMES[0])
        self.write(
            "docs/superpowers/specs/2026-08-08-fkst-ops-extraction-design.md",
            " ".join(NAMES),
        )
        self.assertEqual(0, self.invoke().returncode)


if __name__ == "__main__":
    unittest.main()
