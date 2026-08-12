"""External executables used by the operations mechanism itself."""

from __future__ import annotations

from typing import NamedTuple


class MechanismTool(NamedTuple):
    environment: str | None
    shell_variable: str | None
    required: bool
    child_path: bool = False


MECHANISM_TOOLS = {
    "gh": MechanismTool("FKST_GITHUB_REAL_GH", "REAL_GH", True),
    "gh-app": MechanismTool(
        "FKST_GITHUB_CREDENTIAL_RESOLVER", "GITHUB_CREDENTIAL_RESOLVER", True
    ),
    "lsof": MechanismTool("FKST_OPS_LSOF", "LSOF", False),
    # The engine has no codex executable override and resolves it only on PATH.
    "codex": MechanismTool(None, None, True, child_path=True),
}
