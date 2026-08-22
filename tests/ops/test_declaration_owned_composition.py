"""The declaration may own the platform composition, and the target manifest is the fallback.

These cases discriminate: each fails if the operator reads the wrong source. A suite that passes
identically with and without the ownership change proves nothing about the change, which is why
the declared list here is deliberately different from what a target manifest would yield.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def derive(declared: str) -> subprocess.CompletedProcess:
    """Run the operator's derivation with DECLARED_PLATFORM_PKGS set to `declared`."""
    script = (
        'eval "$(sed -n \'/^derive_devloop_pkgs_from_workspace()/,/^}/p\' '
        f'"{ROOT}/ops/deployment_operator.sh")"\n'
        f'DECLARED_PLATFORM_PKGS={declared!r}\n'
        'HOST=/nonexistent-target PKGSRC=/nonexistent-platform PLATFORM_GIT_URL=x\n'
        f'PYTHON={sys.executable!r}\n'
        f'_self_dir={str(ROOT / "ops")!r}\n'
        'derive_devloop_pkgs_from_workspace probe && printf "%s" "$DEVLOOP_PKGS"\n'
    )
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True)


class DeclarationOwnedCompositionTest(unittest.TestCase):
    def test_a_declared_list_is_used_without_reading_the_target(self) -> None:
        """The target paths are deliberately nonexistent: reaching them at all is the failure."""
        result = derive("alpha beta")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "alpha beta")

    def test_an_absent_declaration_falls_back_to_the_target_and_reports_it(self) -> None:
        """With no declared list the target manifest is consulted, and here it does not exist."""
        result = derive("")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fkst.workspace.toml", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
