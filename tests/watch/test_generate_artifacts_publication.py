from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tomllib

import pytest

from generate_artifacts_test_support import FIXTURES, GIT, ROOT, git, prepared, run_generator, source
from ops.revision_derivation import build_is_current


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_scratch_machine_root_leaves_live_machine_state_untouched(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    live = home / ".fkst" / "machine"
    live.mkdir(parents=True)
    sentinels = [
        live / "profile.toml", live / "declarations.json",
        live / "LaunchAgents" / "com.fkst.cadence.plist",
        live / "roots" / "existing" / "work",
    ]
    for path in sentinels:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("live\n", encoding="ascii")
    scratch = tmp_path / "scratch-machine"
    result = run_generator(repository, home, scratch)
    assert result.returncode == 0, result.stderr
    assert all(path.read_text() == "live\n" for path in sentinels)
    assert not (home / "launchctl.calls").exists()
    assert (scratch / "profile.toml").is_file()
    assert (scratch / "declarations.json").is_file()
    assert (scratch / "LaunchAgents" / "com.fkst.cadence.plist").is_file()


def test_generation_rejects_repository_root_symlink_to_test_fixture(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    hidden = repository / "tests" / "fixtures" / "hidden.toml"
    hidden.parent.mkdir(parents=True)
    hidden.write_text((repository / "deployment.toml").read_text(), encoding="ascii")
    (repository / "adopted.toml").symlink_to(hidden.relative_to(repository))
    (repository / "deployment-set.json").write_text(json.dumps({
        "schema": "fkst.ops.declaration-input.v1", "declarations": ["adopted.toml"],
    }), encoding="ascii")
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "forbidden canonical declaration target" in result.stderr
    assert not (home / ".fkst" / "machine" / "profile.toml").exists()


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_generation_rejects_missing_remote_integration_branch(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    git(tmp_path / "target-source", "branch", "-D", "integration")
    result = run_generator(repository, home)
    assert result.returncode == 2
    assert "remote integration branch integration is missing" in result.stderr


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_package_source_cannot_poison_engine_checkout_before_validation(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    original = declaration.read_text(encoding="ascii")
    package_binding = '''[[deployment.package_sources]]
lock_ref = "engine-source"
checkout = "engine-source"
packages = ["site-board"]

'''
    declaration.write_text(
        original.replace(
            "[deployment.engine_revision]\n",
            package_binding + "[deployment.engine_revision]\n",
        ),
        encoding="ascii",
    )

    rejected = run_generator(repository, home)
    engine_checkout = home / ".fkst" / "machine" / "roots" / "engine-source"

    assert rejected.returncode == 2
    assert "already declared as a deployment source" in rejected.stderr
    assert not engine_checkout.exists()

    declaration.write_text(original, encoding="ascii")
    recovered = run_generator(repository, home)
    assert recovered.returncode == 0, recovered.stderr
    assert git(engine_checkout, "remote", "get-url", "origin") == str(
        tmp_path / "engine-source"
    )


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_path_like_package_source_is_rejected_before_any_machine_artifact(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace(
            "[deployment.engine_revision]\n",
            '''[[deployment.package_sources]]
lock_ref = "package-source"
checkout = "../escaped"
packages = ["github-devloop"]

[deployment.engine_revision]
''',
        ),
        encoding="ascii",
    )
    with (repository / "fkst.lock").open("a", encoding="ascii") as stream:
        stream.write(
            f'''[[external_source]]
id = "package-source"
git = "{tmp_path / 'target-source'}"
checkout_role = "deployment-operated"

'''
        )
    machine = home / ".fkst" / "machine"

    rejected = run_generator(repository, home)

    assert rejected.returncode == 2
    assert "must be a logical name, not a path" in rejected.stderr
    assert not machine.exists(), "invalid input materialised machine state before validation"


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_generation_has_no_deployment_revision_floor(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    source_root = tmp_path / "target-source"
    (source_root / "new-platform-state").write_text("new\n", encoding="ascii")
    git(source_root, "add", "new-platform-state")
    git(source_root, "commit", "-qm", "advance beyond any former floor")
    git(source_root, "branch", "-f", "integration", "HEAD")

    result = run_generator(repository, home)
    assert result.returncode == 0, result.stderr
    checkout = home / ".fkst" / "machine" / "roots" / "packages-host"
    assert git(checkout, "rev-parse", "HEAD") == git(source_root, "rev-parse", "HEAD")


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_hydration_failure_preserves_coherent_live_control_state(tmp_path: Path) -> None:
    repository, home, declaration_data = prepared(tmp_path)
    first = run_generator(repository, home)
    assert first.returncode == 0, first.stderr
    machine = home / ".fkst" / "machine"
    profile = machine / "profile.toml"
    manifest = machine / "declarations.json"
    launch_agent = machine / "LaunchAgents" / "com.fkst.cadence.plist"
    before = {path: path.read_bytes() for path in (profile, manifest, launch_agent)}

    checkout = machine / "roots" / declaration_data["deployment"][0]["machine"]["target_checkout"]
    git(checkout, "checkout", "-qb", "personal")
    failed = run_generator(repository, home)
    assert failed.returncode == 2
    assert {path: path.read_bytes() for path in before} == before

    operator = tmp_path / "operator"
    calls = tmp_path / "scheduled.calls"
    operator.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\nexit 0\n", encoding="ascii"
    )
    operator.chmod(0o755)
    environment = {**os.environ, "CALLS": str(calls)}
    scheduled = subprocess.run(
        [
            sys.executable, str(ROOT / "watch" / "cadence_round.py"),
            "--deployment-repository", str(repository),
            "--machine-profile", str(profile),
            "--declaration-manifest", str(manifest),
            "--ledger", str(tmp_path / "ledger.jsonl"),
            "--guard-restart-attempt-limit", "0",
            "--operator-entry", str(operator),
        ],
        env=environment, text=True, capture_output=True, check=False,
    )
    assert scheduled.returncode == 0, scheduled.stderr
    assert len(calls.read_text().splitlines()) == 2


def test_publication_failure_leaves_only_revision_addressed_engine_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import watch.generate_artifacts as generator

    repository, home, _ = prepared(tmp_path)
    machine = tmp_path / "scratch-machine"
    monkeypatch.setattr(generator, "_verify_mechanism_root", lambda _lock: None)
    monkeypatch.setattr(
        generator,
        "_discover_tools",
        lambda _declarations: {name: sys.executable for name in ("codex", "gh", "gh-app")},
    )
    generator.generate(repository, home, "fkst-bot", machine)
    selected_before = os.readlink(machine / "control" / "current")

    engine_source = tmp_path / "engine-source"
    (engine_source / "new-engine-state").write_text("new\n", encoding="ascii")
    git(engine_source, "add", "new-engine-state")
    git(engine_source, "commit", "-qm", "new engine")
    engine_revision = git(engine_source, "rev-parse", "HEAD")
    platform_source = tmp_path / "target-source"
    (platform_source / ".control" / "engine-ref").write_text(
        engine_revision + "\n", encoding="ascii"
    )
    git(platform_source, "add", ".control/engine-ref")
    git(platform_source, "commit", "-qm", "select new engine")
    git(platform_source, "branch", "-f", "integration", "HEAD")

    def fail_publication(*_args: object, **_kwargs: object) -> None:
        raise ValueError("injected control publication failure")

    monkeypatch.setattr(generator, "_publish_control_files", fail_publication)
    with pytest.raises(ValueError, match="injected control publication failure"):
        generator.generate(repository, home, "fkst-bot", machine)

    binary = machine / "bin" / f"engine-{engine_revision}"
    assert binary.is_file()
    assert not binary.is_symlink()
    assert build_is_current(binary, engine_revision, ["./cargo", "build", "-p", "engine"])
    assert not (machine / "bin" / "engine").exists()
    assert not (machine / "bin" / "engine").is_symlink()
    assert os.readlink(machine / "control" / "current") == selected_before


@pytest.mark.parametrize(
    "checkpoint", ["public-link-1", "public-link-2", "public-link-3", "generation-selected"]
)
def test_abrupt_death_never_exposes_mixed_control_generation(
    tmp_path: Path, checkpoint: str
) -> None:
    import signal
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    destinations = (
        machine / "profile.toml",
        machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
    staging = machine / "staging"
    staging.mkdir()
    candidates = []
    for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
        candidate = staging / name
        candidate.write_bytes(f"new-{index}".encode("ascii"))
        candidates.append(candidate)

    child = os.fork()
    if child == 0:
        generator._publication_checkpoint = lambda point: (
            os.kill(os.getpid(), signal.SIGKILL) if point == checkpoint else None
        )
        generator._publish_control_files(
            dict(zip(destinations, candidates)), destinations[-1], True, False,
            machine / "control",
        )
        os._exit(0)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    observed = tuple(path.read_bytes() if path.exists() else None for path in destinations)
    old = tuple(None for _ in destinations)
    new = tuple(f"new-{index}".encode("ascii") for index in range(3))
    assert observed in (old, new)


def test_generation_retention_protects_selected_and_running_generations(tmp_path: Path) -> None:
    import watch.generate_artifacts as generator

    control = tmp_path / "control"
    generations = control / "generations"
    generations.mkdir(parents=True)
    selected = generations / "selected"
    launch_source = generations / "launch-source"
    profile_reference = generations / "profile-reference"
    manifest_reference = generations / "manifest-reference"
    abandoned = generations / "abandoned"
    for generation in (
        selected, launch_source, profile_reference, manifest_reference, abandoned
    ):
        generation.mkdir()
    (control / "current").symlink_to("generations/selected")
    running_agent = launch_source / "com.fkst.cadence.plist"
    running_agent.write_bytes(plistlib.dumps({"ProgramArguments": [
        str(profile_reference / "profile.toml"),
        str(manifest_reference / "declarations.json"),
    ]}))

    generator._prune_generations(control, generator.ScheduleState(True, running_agent))

    assert {path.name for path in generations.iterdir()} == {
        "selected", "launch-source", "profile-reference", "manifest-reference",
    }


def test_concurrent_publications_are_serialised_per_control_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import fcntl
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    control = machine / "control"
    destinations = (
        machine / "profile.toml", machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    attempted_second = threading.Event()
    release_second_attempt = threading.Event()
    resumed_second_attempt = threading.Event()
    entered_first = threading.Event()
    release_first = threading.Event()
    entered_second = threading.Event()
    errors: list[BaseException] = []

    def checkpoint(point: str) -> None:
        if point == "lock-attempt" and threading.current_thread().name == "publisher-b":
            attempted_second.set()
            assert release_second_attempt.wait(5)
            resumed_second_attempt.set()
            return
        if point != "generation-selected":
            return
        if threading.current_thread().name == "publisher-a":
            entered_first.set()
            assert release_first.wait(5)
        else:
            entered_second.set()

    monkeypatch.setattr(generator, "_publication_checkpoint", checkpoint)

    def publish(label: str) -> None:
        staging = machine / f"staging-{label}"
        staging.mkdir(parents=True)
        candidates = []
        for index, name in enumerate((
            "profile.toml", "declarations.json", "com.fkst.cadence.plist"
        )):
            candidate = staging / name
            candidate.write_bytes(f"{label}-{index}".encode("ascii"))
            candidates.append(candidate)
        try:
            generator._publish_control_files(
                dict(zip(destinations, candidates)), destinations[-1], True, False,
                control, f"generation-{label}",
            )
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=publish, args=("a",), name="publisher-a")
    second = threading.Thread(target=publish, args=("b",), name="publisher-b")
    first.start()
    assert entered_first.wait(5)
    second.start()
    assert attempted_second.wait(5), "second publisher did not reach the lock attempt"
    release_second_attempt.set()
    assert resumed_second_attempt.wait(5), "second publisher did not resume its lock attempt"
    with (control / ".publish.lock").open("a+b") as probe:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not entered_second.is_set(), "second publisher entered the publication transaction"
    release_first.set()
    first.join(5)
    second.join(5)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert entered_second.is_set()
    assert (control / "current").resolve().name == "generation-b"
    assert {path.name for path in (control / "generations").iterdir()} == {"generation-b"}


def test_repeated_isolated_publication_is_bounded(tmp_path: Path) -> None:
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    destinations = (
        machine / "profile.toml", machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    for run in range(4):
        staging = machine / f"staging-{run}"
        staging.mkdir(parents=True)
        candidates = []
        for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
            candidate = staging / name
            candidate.write_bytes(f"{run}-{index}".encode("ascii"))
            candidates.append(candidate)
        generator._publish_control_files(
            dict(zip(destinations, candidates)), destinations[-1], True, False,
            machine / "control",
        )
        assert len(list((machine / "control" / "generations").iterdir())) == 1


def _reconciliation_launchctl(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "launchctl"
    state = tmp_path / "schedule.state"
    calls = tmp_path / "schedule.calls"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "state=pathlib.Path(os.environ['LAUNCHCTL_STATE'])\n"
        "calls=pathlib.Path(os.environ['LAUNCHCTL_CALLS'])\n"
        "with calls.open('a') as stream: stream.write(' '.join(sys.argv[1:])+'\\n')\n"
        "command=sys.argv[1]\n"
        "if command == 'print':\n"
        "  if state.exists(): print('path = '+state.read_text().strip()); raise SystemExit(0)\n"
        "  raise SystemExit(113)\n"
        "if command == 'bootout': state.unlink(missing_ok=True); raise SystemExit(0)\n"
        "if command == 'enable': raise SystemExit(0)\n"
        "if command == 'bootstrap':\n"
        "  source=sys.argv[-1]\n"
        "  if source != os.environ['LEGACY_PATH'] or os.environ.get('RESTORE_FAIL') == '1':\n"
        "    print('injected bootstrap failure', file=sys.stderr); raise SystemExit(1)\n"
        "  state.write_text(source+'\\n'); raise SystemExit(0)\n",
        encoding="ascii",
    )
    executable.chmod(0o755)
    return executable, state


@pytest.mark.parametrize("restoration_fails", [False, True])
def test_reconciliation_failure_restores_exact_legacy_schedule_or_surfaces_both_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restoration_fails: bool
) -> None:
    import watch.generate_artifacts as generator

    machine = tmp_path / "machine"
    legacy = tmp_path / "Library" / "LaunchAgents" / "com.fkst.cadence.plist"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy agent", encoding="ascii")
    executable, state = _reconciliation_launchctl(tmp_path)
    state.write_text(str(legacy), encoding="ascii")
    monkeypatch.setenv("FKST_LAUNCHCTL", str(executable))
    monkeypatch.setenv("LAUNCHCTL_STATE", str(state))
    monkeypatch.setenv("LAUNCHCTL_CALLS", str(tmp_path / "schedule.calls"))
    monkeypatch.setenv("LEGACY_PATH", str(legacy))
    monkeypatch.setenv("RESTORE_FAIL", "1" if restoration_fails else "0")

    destinations = (
        machine / "profile.toml", machine / "declarations.json",
        machine / "LaunchAgents" / "com.fkst.cadence.plist",
    )
    staging = machine / "staging"
    staging.mkdir(parents=True)
    candidates = []
    for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
        candidate = staging / name
        candidate.write_bytes(f"new-{index}".encode("ascii"))
        candidates.append(candidate)

    with pytest.raises(ValueError) as raised:
        generator._publish_control_files(
            dict(zip(destinations, candidates)), destinations[-1], True, True,
            machine / "control",
        )
    if restoration_fails:
        assert "publication failed: cannot activate cadence schedule" in str(raised.value)
        assert "schedule restoration failed: cannot activate cadence schedule" in str(raised.value)
        assert not state.exists()
    else:
        assert state.read_text().strip() == str(legacy)
        assert f"bootstrap gui/{os.getuid()} {legacy}" in (tmp_path / "schedule.calls").read_text()
    assert not any(path.exists() for path in destinations)
    # The legacy source is deliberately not a plist, so reference evidence cannot
    # be obtained and pruning fails closed rather than guessing about a live job.
    generations = machine / "control" / "generations"
    assert len(list(generations.iterdir())) == (0 if restoration_fails else 1)

    if not restoration_fails:
        monkeypatch.setenv("RESTORE_FAIL", "0")
        state.unlink()
        retry_staging = machine / "retry-staging"
        retry_staging.mkdir()
        retry_candidates = []
        for index, name in enumerate(("profile.toml", "declarations.json", "com.fkst.cadence.plist")):
            candidate = retry_staging / name
            candidate.write_bytes(f"retry-{index}".encode("ascii"))
            retry_candidates.append(candidate)
        generator._publish_control_files(
            dict(zip(destinations, retry_candidates)), destinations[-1], True, False,
            machine / "control",
        )
        assert len(list(generations.iterdir())) == 1


def test_profile_uses_explicit_bot_login_and_does_not_write_managed_bot_set(
    tmp_path: Path,
) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    declaration["deployment"][0]["managed_bot_logins"] = ["bot-a", "bot-b"]
    from watch.generate_artifacts import _profile_text

    profile = tomllib.loads(_profile_text(
        [(tmp_path / "declaration.toml", declaration)], tmp_path, {}, bot_login="bot-b"
    ))

    assert profile["credentials"] == {"github-bot": "bot-b"}
    assert "sets" not in profile


def test_profile_emits_new_engine_checkout_root_from_declaration(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    declaration["deployment"][0]["machine"]["engine_checkout"] = "new-engine-checkout"
    from watch.generate_artifacts import _profile_text

    profile = tomllib.loads(_profile_text(
        [(tmp_path / "declaration.toml", declaration)],
        tmp_path,
        {},
        bot_login="fkst-bot",
    ))

    assert profile["roots"]["new-engine-checkout"] == str(
        tmp_path / "roots" / "new-engine-checkout"
    )


@pytest.mark.parametrize("kind", ["branch", "engine"])
def test_existing_checkout_with_wrong_origin_is_rejected(
    tmp_path: Path, kind: str
) -> None:
    from watch.source_hydration import (
        BranchCheckout,
        EngineCheckout,
        _materialise_branch_checkout,
        _materialise_engine_checkout,
    )

    declared = tmp_path / "declared"
    revision = source(declared, {"entry": "fixture\n"})
    git(declared, "branch", "integration")
    alternate = tmp_path / "alternate"
    subprocess.run([GIT, "clone", "-q", str(declared), str(alternate)], check=True)
    git(alternate, "branch", "integration")
    checkout = tmp_path / "checkout"
    subprocess.run([GIT, "clone", "-q", str(alternate), str(checkout)], check=True)

    with pytest.raises(ValueError, match="CHECKOUT_SOURCE_MISMATCH"):
        if kind == "branch":
            git(checkout, "checkout", "-q", "integration")
            _materialise_branch_checkout(
                checkout, BranchCheckout(str(declared), "integration")
            )
        else:
            git(checkout, "checkout", "--detach", "-q", revision)
            _materialise_engine_checkout(
                checkout, EngineCheckout(str(declared), revision)
            )


def test_profile_populates_every_referenced_machine_default(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    deployment = declaration["deployment"][0]
    deployment["integration"]["integration_branch"] = "machine:first-branch"
    second = copy.deepcopy(deployment)
    second["integration"]["integration_branch"] = "machine:second-branch"
    declaration["deployment"].append(second)
    from watch.generate_artifacts import _profile_text

    profile = tomllib.loads(_profile_text(
        [(tmp_path / "declaration.toml", declaration)],
        tmp_path,
        {},
        bot_login="fkst-bot[bot]",
    ))

    assert profile["defaults"] == {
        "first-branch": "integration-fkst-bot",
        "second-branch": "integration-fkst-bot",
    }


def test_generator_requires_explicit_bot_login_cli_argument(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    result = run_generator(repository, home, bot_login=None)
    assert result.returncode == 2
    assert "--bot-login" in result.stderr
    assert "required" in result.stderr


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_generator_rejects_bot_login_outside_declared_roster(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    result = run_generator(repository, home, bot_login="outside-bot")
    assert result.returncode == 2
    assert "outside-bot is not in" in result.stderr
    assert "managed_bot_logins" in result.stderr


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_generator_rejects_bot_login_with_empty_normalized_identity(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    result = run_generator(repository, home, bot_login="[bot]")
    assert result.returncode == 2
    assert "--bot-login: must not normalize to an empty identity" in result.stderr


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_generator_normalizes_bot_suffix_without_changing_login_case(tmp_path: Path) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    declaration.write_text(
        declaration.read_text(encoding="ascii").replace(
            'managed_bot_logins = ["fkst-bot"]',
            'managed_bot_logins = ["Managed-Bot"]',
        ),
        encoding="ascii",
    )

    accepted = run_generator(repository, home, bot_login="Managed-Bot[bot]")
    assert accepted.returncode == 0, accepted.stderr
    profile = tomllib.loads(
        (home / ".fkst" / "machine" / "profile.toml").read_text(encoding="ascii")
    )
    assert profile["credentials"]["github-bot"] == "Managed-Bot[bot]"

    rejected = run_generator(repository, home, bot_login="managed-bot[bot]")
    assert rejected.returncode == 2
    assert "is not in" in rejected.stderr


@pytest.mark.usefixtures("fabricated_mechanism_tools")
def test_profileless_missing_machine_bot_login_is_a_typed_generation_error(
    tmp_path: Path,
) -> None:
    repository, home, _ = prepared(tmp_path)
    declaration = repository / "deployment.toml"
    text = declaration.read_text(encoding="ascii")
    profile_block = '''[deployment.github_devloop_profile]
version = "1"
id = "github-devloop-default"
data = { rollup_label = "rollup" }
producer_binding = "github-board"

'''
    declaration.write_text(
        text.replace(profile_block, "").replace('bot_login = "github-bot"\n', ""),
        encoding="ascii",
    )

    result = run_generator(repository, home)

    assert result.returncode == 2
    assert "artifact generation failed" in result.stderr
    assert "deployment[0].machine.bot_login is required for artifact generation" in result.stderr
    assert "Traceback" not in result.stderr


def test_profile_rejects_empty_or_malformed_declared_bot_roster(tmp_path: Path) -> None:
    declaration = tomllib.loads((FIXTURES / "packages.toml").read_text())
    home = tmp_path / "home"
    home.mkdir()
    for value in ([], ["valid", ""]):
        declaration["deployment"][0]["managed_bot_logins"] = value
        try:
            from watch.generate_artifacts import _profile_text
            _profile_text(
                [(tmp_path / "declaration.toml", declaration)], home, {}, bot_login="valid"
            )
        except ValueError as exc:
            assert "non-empty string list" in str(exc)
        else:
            raise AssertionError("invalid bot roster declaration was accepted")


def test_engine_checkout_is_materialised_detached(tmp_path: Path) -> None:
    from watch.source_hydration import EngineCheckout, _materialise_engine_checkout

    engine = tmp_path / "engine-source"
    revision = source(engine, {"bin/entry": "#!/bin/sh\nexit 0\n"})
    checkout = tmp_path / "engine-checkout"
    _materialise_engine_checkout(
        checkout, EngineCheckout(str(engine), revision)
    )
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert git(checkout, "branch", "--show-current") == ""


def test_pinned_deployment_checkout_detaches_an_existing_branch(
    tmp_path: Path,
) -> None:
    from watch.source_hydration import EngineCheckout, _materialise_engine_checkout

    source_root = tmp_path / "platform-source"
    revision = source(source_root, {"state": "pinned\n"})
    git(source_root, "branch", "integration")
    checkout = tmp_path / "platform-checkout"
    subprocess.run([GIT, "clone", "-q", str(source_root), str(checkout)], check=True)
    _materialise_engine_checkout(
        checkout, EngineCheckout(str(source_root), revision), allow_attached=True
    )
    assert git(checkout, "rev-parse", "HEAD") == revision
    assert git(checkout, "branch", "--show-current") == ""

    state = checkout / "state"
    state.write_text("operator edit\n", encoding="ascii")
    _materialise_engine_checkout(
        checkout, EngineCheckout(str(source_root), revision), allow_attached=True
    )
    assert state.read_text(encoding="ascii") == "operator edit\n"
