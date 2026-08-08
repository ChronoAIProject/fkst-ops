"""Published provider entry points supplied by the pinned fkst-ops mechanism."""

from __future__ import annotations


# provider-published-surface: This map is a versioned declaration contract. Files
# in the mechanism checkout are private unless their path and kind appear here.
MECHANISM_SOURCE_ID = "fkst-ops"
PUBLISHED_PROVIDER_ENTRY_POINTS = {
    "providers/github_credential_gh.py": "credential.github",
    "providers/engine.py": "engine",
    "providers/board_engine_durable.py": "board.engine-durable",
    "providers/board_github_control.sh": "board.github-control",
}
