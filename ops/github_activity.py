"""Typed observation of one exact GitHub issue or pull request's activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable, Protocol
from urllib.parse import urlparse

from ops.probe_result import ProbeFailure, ProbeResult


COMMAND_TIMEOUT_SECONDS = 30
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z")


class GithubInstrumentFailure(Exception):
    """The GitHub instrument could not produce a valid entity fact."""


@dataclass(frozen=True)
class GithubEntityFact:
    repository: str
    entity_number: int
    node_id: str
    entity_url: str
    entity_kind: str
    updated_at_utc: str


class GithubInstrument(Protocol):
    name: str

    def inspect(self, repository: str, entity_number: int) -> GithubEntityFact:
        ...


def _repository_from_api_url(value: object) -> str:
    if not isinstance(value, str):
        raise GithubInstrumentFailure("GitHub entity has no repository URL")
    parsed = urlparse(value)
    parts = parsed.path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or len(parts) != 4
        or parts[1] != "repos"
        or not parts[2]
        or not parts[3]
    ):
        raise GithubInstrumentFailure("GitHub entity repository URL is not canonical")
    return f"{parts[2]}/{parts[3]}"


class CredentialedGithubInstrument:
    name = "credentialed-gh-api"

    def __init__(self, command: tuple[str, ...], environment: dict[str, str]):
        self.command = command
        self.environment = environment

    @classmethod
    def from_deployment(
        cls, deployment: dict[str, object], repository: str
    ) -> CredentialedGithubInstrument:
        machine = deployment.get("machine")
        providers = deployment.get("providers")
        if not isinstance(machine, dict) or not isinstance(providers, dict):
            raise ProbeFailure(
                "selector_invalid", "resolved deployment has no GitHub instrument binding"
            )
        binding = providers.get("github_credential")
        if not isinstance(binding, dict):
            raise ProbeFailure(
                "selector_invalid", "resolved deployment has no GitHub credential binding"
            )
        configuration = binding.get("configuration")
        helper = binding.get("executable")
        bot_login = machine.get("bot_login")
        if not isinstance(configuration, dict):
            raise ProbeFailure(
                "selector_invalid", "resolved GitHub credential binding has no configuration"
            )
        source = configuration.get("source")
        if not all(isinstance(value, str) and value for value in (helper, bot_login, source)):
            raise ProbeFailure(
                "selector_invalid", "resolved GitHub instrument identity is incomplete"
            )
        environment = os.environ.copy()
        environment.pop("GH_TOKEN", None)
        environment.pop("GITHUB_TOKEN", None)
        environment.update({
            "FKST_GITHUB_CREDENTIAL_HELPER": helper,
            "FKST_GITHUB_CREDENTIAL_SOURCE": source,
            "FKST_GITHUB_REAL_GH": environment.get("FKST_GITHUB_REAL_GH", environment.get("REAL_GH", "")),
            "FKST_GITHUB_CREDENTIAL_RESOLVER": environment.get(
                "FKST_GITHUB_CREDENTIAL_RESOLVER", environment.get("GITHUB_CREDENTIAL_RESOLVER", "")
            ),
            "FKST_GITHUB_REPO": repository,
            "FKST_GITHUB_BOT_LOGIN": bot_login,
        })
        proxy = Path(__file__).with_name("github_credential_gh.py")
        return cls((sys.executable, str(proxy)), environment)

    def inspect(self, repository: str, entity_number: int) -> GithubEntityFact:
        endpoint = f"repos/{repository}/issues/{entity_number}"
        command = [*self.command, "api", "--method", "GET", endpoint]
        try:
            completed = subprocess.run(
                command,
                env=self.environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GithubInstrumentFailure(f"GitHub entity query could not complete: {exc}") from exc
        if completed.returncode != 0:
            diagnostic = completed.stderr.strip() or "no diagnostic"
            raise GithubInstrumentFailure(
                f"GitHub entity query failed with exit {completed.returncode}: {diagnostic}"
            )
        try:
            document = json.loads(completed.stdout)
        except (ValueError, RecursionError) as exc:
            raise GithubInstrumentFailure(f"GitHub entity query returned invalid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise GithubInstrumentFailure("GitHub entity query did not return an object")
        number = document.get("number")
        node_id = document.get("node_id")
        entity_url = document.get("html_url")
        updated_at = document.get("updated_at")
        if type(number) is not int or not isinstance(node_id, str) or not node_id:
            raise GithubInstrumentFailure("GitHub entity response has an invalid exact identity")
        if not isinstance(entity_url, str) or not entity_url:
            raise GithubInstrumentFailure("GitHub entity response has no canonical entity URL")
        if not isinstance(updated_at, str) or not updated_at:
            raise GithubInstrumentFailure("GitHub entity response has no activity time")
        return GithubEntityFact(
            repository=_repository_from_api_url(document.get("repository_url")),
            entity_number=number,
            node_id=node_id,
            entity_url=entity_url,
            entity_kind="pull_request" if "pull_request" in document else "issue",
            updated_at_utc=updated_at,
        )


def _epoch_ns(value: str, label: str) -> int:
    if not isinstance(value, str) or UTC_TIME.fullmatch(value) is None:
        raise ProbeFailure(
            "invalid_time",
            f"{label} must be an explicit UTC RFC3339 instant ending in Z",
            supplied_time=value,
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProbeFailure(
            "invalid_time", f"{label} is not a calendar-valid UTC instant", supplied_time=value
        ) from exc
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = parsed - epoch
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def _selector(
    deployment: dict[str, object], entity_number: str | int
) -> tuple[dict[str, object], int]:
    deployment_id = deployment.get("id")
    repository = deployment.get("target_identity")
    if not isinstance(deployment_id, str) or not deployment_id:
        raise ProbeFailure("selector_invalid", "resolved deployment has no exact identity")
    if not isinstance(repository, str) or REPOSITORY.fullmatch(repository) is None:
        raise ProbeFailure(
            "selector_invalid", "resolved target identity is not an exact owner/repository"
        )
    if type(entity_number) is int:
        number = entity_number
    elif isinstance(entity_number, str) and re.fullmatch(r"[1-9][0-9]*", entity_number):
        number = int(entity_number)
    else:
        raise ProbeFailure(
            "selector_invalid", "GitHub entity number must be a positive decimal integer",
            supplied_entity=entity_number,
        )
    if number <= 0:
        raise ProbeFailure(
            "selector_invalid", "GitHub entity number must be positive", supplied_entity=entity_number
        )
    return {
        "deployment": deployment_id,
        "target_identity": repository,
        "repository": repository,
        "entity_number": number,
        "entity_node_id": None,
        "entity_url": None,
        "entity_kind": None,
    }, number


def _failure_result(
    identity: dict[str, object],
    now_ns: int,
    since_utc: str | None,
    since_ns: int | None,
    instrument_name: str | None,
    failure: ProbeFailure,
    *,
    selector_validated: bool,
    time_validated: bool,
) -> ProbeResult:
    return ProbeResult(
        probe="github_activity",
        state="unknown",
        identity=identity,
        observations={"activity_at": None},
        time={"basis": "unix_epoch_ns", "now": now_ns, "since": since_ns,
              "since_utc": since_utc, "entity_updated": None, "clock": "time.time_ns"},
        provenance={"configuration": "schema.validator resolved declaration",
                    "github_instrument": instrument_name},
        coverage={"scope": "one exact GitHub issue or pull request updated_at",
                  "complete": False, "truncated": False, "pages": 0,
                  "selector_validated": selector_validated,
                  "time_conversion_validated": time_validated},
        failure=failure.as_dict(),
    )


def probe_github_activity(
    deployment: dict[str, object],
    entity_number: str | int,
    since_utc: str,
    instrument: GithubInstrument | None = None,
    now_epoch_ns: int | None = None,
    clock: Callable[[], int] = time.time_ns,
) -> ProbeResult:
    """Answer activity from the selected entity's server-maintained updated_at."""
    now_ns = clock() if now_epoch_ns is None else now_epoch_ns
    empty_identity = {
        "deployment": deployment.get("id") if isinstance(deployment.get("id"), str) else None,
        "target_identity": None, "repository": None, "entity_number": None,
        "entity_node_id": None, "entity_url": None, "entity_kind": None,
    }
    instrument_name = instrument.name if instrument is not None else CredentialedGithubInstrument.name
    if type(now_ns) is not int or now_ns <= 0:
        failure = ProbeFailure(
            "invalid_time", "captured system time is not a positive epoch nanosecond", captured_now=now_ns
        )
        return _failure_result(
            empty_identity, now_ns, None, None, instrument_name, failure,
            selector_validated=False, time_validated=False,
        )
    try:
        identity, number = _selector(deployment, entity_number)
    except ProbeFailure as failure:
        return _failure_result(
            empty_identity, now_ns, None, None, instrument_name, failure,
            selector_validated=False, time_validated=False,
        )
    try:
        since_ns = _epoch_ns(since_utc, "since")
    except ProbeFailure as failure:
        return _failure_result(
            identity, now_ns, None, None, instrument_name, failure,
            selector_validated=True, time_validated=False,
        )
    if since_ns > now_ns:
        failure = ProbeFailure(
            "invalid_time", "since instant is after the captured system time",
            since=since_ns, captured_now=now_ns,
        )
        return _failure_result(
            identity, now_ns, since_utc, since_ns, instrument_name, failure,
            selector_validated=True, time_validated=False,
        )
    if instrument is None:
        try:
            instrument = CredentialedGithubInstrument.from_deployment(
                deployment, str(identity["repository"])
            )
        except ProbeFailure as failure:
            return _failure_result(
                identity, now_ns, since_utc, since_ns, instrument_name, failure,
                selector_validated=False, time_validated=True,
            )
    try:
        fact = instrument.inspect(str(identity["repository"]), number)
    except GithubInstrumentFailure as exc:
        failure = ProbeFailure("instrument_failure", str(exc), operation="github_entity_query")
        return _failure_result(
            identity, now_ns, since_utc, since_ns, instrument.name, failure,
            selector_validated=True, time_validated=True,
        )
    if (
        fact.repository.casefold() != str(identity["repository"]).casefold()
        or fact.entity_number != number
    ):
        failure = ProbeFailure(
            "identity_failure", "GitHub instrument answered for a different entity",
            requested_repository=identity["repository"], requested_entity_number=number,
            observed_repository=fact.repository, observed_entity_number=fact.entity_number,
        )
        return _failure_result(
            identity, now_ns, since_utc, since_ns, instrument.name, failure,
            selector_validated=False, time_validated=True,
        )
    expected_path = "/pull/" if fact.entity_kind == "pull_request" else "/issues/"
    expected_url = f"https://github.com/{fact.repository}{expected_path}{number}"
    if fact.entity_url != expected_url:
        failure = ProbeFailure(
            "identity_failure", "GitHub instrument returned a different canonical entity URL",
            requested_entity_url=expected_url, observed_entity_url=fact.entity_url,
        )
        return _failure_result(
            identity, now_ns, since_utc, since_ns, instrument.name, failure,
            selector_validated=False, time_validated=True,
        )
    try:
        updated_ns = _epoch_ns(fact.updated_at_utc, "GitHub updated_at")
    except ProbeFailure as failure:
        return _failure_result(
            identity, now_ns, since_utc, since_ns, instrument.name, failure,
            selector_validated=True, time_validated=False,
        )
    if updated_ns > now_ns:
        failure = ProbeFailure(
            "invalid_time", "GitHub activity time is after the captured system time",
            entity_updated=updated_ns, captured_now=now_ns,
        )
        return _failure_result(
            identity, now_ns, since_utc, since_ns, instrument.name, failure,
            selector_validated=True, time_validated=False,
        )
    resolved_identity = {
        **identity,
        "repository": fact.repository,
        "entity_node_id": fact.node_id,
        "entity_url": fact.entity_url,
        "entity_kind": fact.entity_kind,
    }
    return ProbeResult(
        probe="github_activity",
        state="present" if updated_ns >= since_ns else "absent",
        identity=resolved_identity,
        observations={"activity_at": fact.updated_at_utc},
        time={"basis": "unix_epoch_ns", "now": now_ns, "since": since_ns,
              "since_utc": since_utc, "entity_updated": updated_ns,
              "clock": "time.time_ns"},
        provenance={"configuration": "schema.validator resolved declaration",
                    "github_instrument": instrument.name,
                    "endpoint": f"repos/{identity['repository']}/issues/{number}",
                    "query": "GitHub server-maintained entity updated_at"},
        coverage={"scope": "one exact GitHub issue or pull request updated_at",
                  "complete": True, "truncated": False, "pages": 1,
                  "selector_validated": True, "time_conversion_validated": True},
    )
