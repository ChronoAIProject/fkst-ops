from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from schema.mechanism_tools import MECHANISM_TOOLS


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "operate-fkst-deployment" / "SKILL.md"
SKILL_SPEC = SKILL.with_name("SPEC.md")


def test_skill_mechanism_tool_claims_match_table() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    binding = re.search(
        r"`(?P<profile_only>[^`]+)` in particular accepts \*\*no\*\*\s+"
        r"environment override.*?`(?P<override_one>[^`]+)` and "
        r"`(?P<override_two>[^`]+)`\s+accept environment overrides",
        skill,
        flags=re.DOTALL,
    )
    assert binding is not None, "cannot parse mechanism-tool claims from SKILL.md"

    expected = {binding["profile_only"]: "profile-only"}
    expected.update(
        (binding[name], "environment-override")
        for name in ("override_one", "override_two")
    )
    actual: dict[str, str] = {}
    for name in expected:
        tool = MECHANISM_TOOLS.get(name)
        if tool is None:
            actual[name] = "missing"
        elif tool.environment is None and tool.shell_variable is None:
            actual[name] = "profile-only"
        elif tool.environment is not None and tool.shell_variable is not None:
            actual[name] = "environment-override"
        else:
            actual[name] = "inconsistent-binding"

    assert actual == expected


def test_skill_spec_test_node_ids_collect() -> None:
    spec = SKILL_SPEC.read_text(encoding="utf-8")
    node_ids = list(dict.fromkeys(re.findall(r"`(tests/[^`]+\.py::[^`]+)`", spec)))
    assert node_ids, "SPEC.md contains no executable evidence node IDs"

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *node_ids],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
