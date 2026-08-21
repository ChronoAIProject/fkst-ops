"""A machine may state its own integration branch; generation must accept the statement.

The branch was always derived as `integration-<normalized_login(bot_login)>`. GitHub logins
are case-insensitive and git refs are not, so a host whose login carries capitals derives a
branch that does not exist on the remote, and generation fails closed against its own output.
Such a host cannot be generated for at all — no cadence agent, and no materialised engine
checkout.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib

import pytest

from tests.watch.generate_artifacts_test_support import (
    git,
    prepared,
    run_generator,
)


def machine_defaults(home: Path) -> dict[str, str]:
    profile = home / ".fkst" / "machine" / "profile.toml"
    return tomllib.loads(profile.read_text(encoding="ascii"))["defaults"]


def declare_machine_branch(
    repository: Path, logical: str = "release-track", managed_login: str | None = None
) -> None:
    declaration = repository / "deployment.toml"
    text = declaration.read_text(encoding="ascii").replace(
        'integration_branch = "integration"',
        f'integration_branch = "machine:{logical}"',
    )
    if managed_login is not None:
        text = text.replace(
            'managed_bot_logins = ["fkst-bot"]', f'managed_bot_logins = ["{managed_login}"]'
        )
    declaration.write_text(text, encoding="ascii")


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_declared_branch_is_used_instead_of_the_derived_one(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    # Capitals in the login are exactly the case the derivation cannot serve.
    bot_login = "MixedCase"
    declare_machine_branch(repository, managed_login=bot_login)
    for source_root in (tmp_path / "target-source", tmp_path / "engine-source"):
        git(source_root, "branch", "integration-mixedcase")

    result = run_generator(
        repository, home, bot_login=bot_login, integration_branch="integration-mixedcase"
    )

    assert result.returncode == 0, result.stderr
    assert machine_defaults(home) == {"release-track": "integration-mixedcase"}


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_omitting_the_flag_still_derives_from_the_login(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declare_machine_branch(repository)
    for source_root in (tmp_path / "target-source", tmp_path / "engine-source"):
        git(source_root, "branch", "integration-fkst-bot")

    result = run_generator(repository, home)

    assert result.returncode == 0, result.stderr
    assert machine_defaults(home) == {"release-track": "integration-fkst-bot"}


@pytest.mark.parametrize(
    "branch",
    [
        "@",
        "@{-1}",
        "+topic",
        "refs/heads/topic",
        "topic..other",
        "topic.lock",
        "topic.",
        "",
    ],
)
@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_names_that_could_resolve_as_revision_syntax_are_rejected(
    tmp_path: Path, branch: str
) -> None:
    # Validating an arbitrary ref by rejection does not converge: each of these satisfies
    # some ref-format check while still being ambiguous at a consumer. The accepted
    # character class is closed instead, so this list is illustrative, not exhaustive.
    repository, home, _ = prepared(tmp_path)
    declare_machine_branch(repository)

    result = run_generator(repository, home, integration_branch=branch)

    assert result.returncode == 2
    # Assert the validator spoke, not merely that the run failed: against an
    # implementation without the flag, argparse also exits 2 with the flag name in
    # stderr, so "--integration-branch in stderr" alone proves nothing.
    assert "artifact generation failed" in result.stderr, result.stderr
    assert "unrecognized arguments" not in result.stderr
    assert "--integration-branch must" in result.stderr


def test_validator_rejects_a_leading_dash_that_argument_parsing_never_forwards() -> None:
    # A leading dash is refused by argument parsing before the validator sees it, so this
    # covers the validator directly rather than asserting a message the CLI never emits.
    import importlib.util

    specification = importlib.util.spec_from_file_location(
        "generate_artifacts", Path(__file__).resolve().parents[2] / "watch" / "generate_artifacts.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # dataclass resolution in the loaded module needs it registered first.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)

    with pytest.raises(ValueError, match="--integration-branch must"):
        module.validate_declared_integration_branch("-force")
    assert module.validate_declared_integration_branch("integration-elonsg") == "integration-elonsg"
