#!/usr/bin/env python3
"""Mint and verify a configured GitHub credential."""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import uuid


TOKEN_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")
COMMAND_TIMEOUT_SECONDS = 30
ATTESTATION_TTL_SECONDS = 60
ATTESTATION_LOCK_WAIT_SECONDS = 0.25
ATTESTATION_LOCK_RETRY_SECONDS = 0.01
ATTESTATION_SCHEMA_VERSION = 2
ATTESTATION_MAX_BYTES = 64 * 1024
ATTESTATION_DIRECTORY_NAME = ".github-cli-user-attestation"
ATTESTATION_STATE_NAME = "verified.json"
ATTESTATION_LOCK_NAME = "verified.lock"
BOOT_SESSION_ID_COMMAND = ("/usr/sbin/sysctl", "-n", "kern.bootsessionuuid")
ATTESTATION_FIELDS = {
    "schema_version", "source", "login", "target", "token_fingerprint",
    "boot_session_id", "verified_monotonic",
}


def fail(cause: str, error: str, *, command: list[str] | None = None) -> int:
    frame = inspect.currentframe()
    assert frame is not None and frame.f_back is not None
    caller = frame.f_back
    source = Path(caller.f_code.co_filename).resolve().relative_to(Path(__file__).resolve().parents[1])
    command_text = f" command={shlex.join(command)}" if command else ""
    print(f"credential-source-failed: {cause} origin={source}:{caller.f_lineno}{command_text} raw_error={error}",
          file=sys.stderr)
    return 1


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in TOKEN_NAMES:
        environment.pop(name, None)
    return environment


def run(arguments: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str] | Exception:
    try:
        return subprocess.run(
            arguments, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return error


def current_boot_session_id() -> str | None:
    completed = run(list(BOOT_SESSION_ID_COMMAND), env=clean_environment())
    if isinstance(completed, Exception) or completed.returncode != 0:
        return None
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        return None
    try:
        return str(uuid.UUID(lines[0]))
    except (AttributeError, ValueError):
        return None


def current_monotonic_time() -> float | None:
    try:
        value = time.clock_gettime(time.CLOCK_MONOTONIC)
    except (OSError, ValueError):
        return None
    return value if math.isfinite(value) else None


def attestation_paths() -> tuple[Path, Path] | None:
    runtime_root = os.environ.get("FKST_RUNTIME_ROOT", "")
    if not runtime_root:
        return None
    directory = Path(runtime_root) / ATTESTATION_DIRECTORY_NAME
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            return None
        os.chmod(directory, 0o700)
    except OSError:
        return None
    return directory / ATTESTATION_STATE_NAME, directory / ATTESTATION_LOCK_NAME


def read_attestation(path: Path) -> dict[str, object] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > ATTESTATION_MAX_BYTES:
            return None
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(ATTESTATION_MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > ATTESTATION_MAX_BYTES:
        return None
    try:
        document = json.loads(payload)
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict) or set(document) != ATTESTATION_FIELDS:
        return None
    return document


def attestation_matches(
    path: Path, *, source: str, login: str, target: str,
    token_fingerprint: str, boot_session_id: str, monotonic_now: float,
) -> bool:
    document = read_attestation(path)
    if document is None:
        return False
    if type(document["schema_version"]) is not int:
        return False
    verified_monotonic = document["verified_monotonic"]
    if type(verified_monotonic) not in {int, float}:
        return False
    try:
        age = monotonic_now - verified_monotonic
    except (OverflowError, TypeError):
        return False
    return (
        document["schema_version"] == ATTESTATION_SCHEMA_VERSION
        and document["source"] == source
        and document["login"] == login
        and document["target"] == target
        and document["token_fingerprint"] == token_fingerprint
        and document["boot_session_id"] == boot_session_id
        and math.isfinite(age)
        and 0 <= age < ATTESTATION_TTL_SECONDS
    )


def acquire_attestation_lock(path: Path) -> int | None:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + ATTESTATION_LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    os.close(descriptor)
                    return None
                time.sleep(min(ATTESTATION_LOCK_RETRY_SECONDS, remaining))
    except OSError:
        os.close(descriptor)
        raise


def publish_attestation(
    path: Path, *, source: str, login: str, target: str,
    token_fingerprint: str, boot_session_id: str, verified_monotonic: float,
) -> None:
    document = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "source": source,
        "login": login,
        "target": target,
        "token_fingerprint": token_fingerprint,
        "boot_session_id": boot_session_id,
        "verified_monotonic": verified_monotonic,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(document, handle, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except (OSError, TypeError, ValueError):
        pass
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


AUTHENTICATION_REFUSAL_STATUSES = frozenset({401, 403})


def observed_http_status(real_gh: str, path: str, env: dict[str, str]) -> int | None:
    """Return the HTTP status GitHub answered with, or None when no answer was observed.

    `gh api` exits 1 for every non-2xx response, so the exit status cannot separate "this
    token is refused" from "GitHub did not serve the request". The status line that `-i`
    prints as its first stdout line can.
    """
    probe = run([real_gh, "api", path, "-i"], env=env)
    if isinstance(probe, Exception):
        return None
    for line in probe.stdout.splitlines():
        if not line.startswith("HTTP/"):
            continue
        fields = line.split()
        return int(fields[1]) if len(fields) >= 2 and fields[1].isdigit() else None
    return None


def verification_failure(
    real_gh: str, kind: str, path: str, env: dict[str, str], command: list[str], detail: str
) -> int:
    """Report a failed verification as what was established, not as the exit status.

    Only a status GitHub actually answered with refutes the credential. Anything else —
    a 5xx, a transport error, an unparseable response — means the question went unanswered,
    which is not evidence that authentication failed.
    """
    status = observed_http_status(real_gh, path, env)
    answered = "no HTTP status observed" if status is None else f"GitHub answered {status}"
    if status in AUTHENTICATION_REFUSAL_STATUSES:
        return fail(f"github-cli-user-{kind}-verification-failed", f"{detail}; {answered}",
                    command=command)
    return fail(f"github-cli-user-{kind}-verification-unavailable", f"{detail}; {answered}",
                command=command)


def verify_github_cli_user(real_gh: str, expected: str, target: str, token: str) -> int:
    verification_environment = clean_environment()
    verification_environment["GH_HOST"] = "github.com"
    verification_environment.pop("GH_ENTERPRISE_TOKEN", None)
    verification_environment.pop("GITHUB_ENTERPRISE_TOKEN", None)
    verification_environment["GH_TOKEN"] = token
    login_command = [real_gh, "api", "/user", "--jq", ".login"]
    login = run(login_command, env=verification_environment)
    if isinstance(login, Exception):
        return verification_failure(real_gh, "login", "/user", verification_environment,
                                    login_command, str(login))
    if login.returncode != 0:
        return verification_failure(
            real_gh, "login", "/user", verification_environment, login_command,
            f"GitHub CLI exited with status {login.returncode}",
        )
    if login.stdout.splitlines() != [expected]:
        return fail(
            "github-cli-user-login-mismatch",
            "verified GitHub login did not exactly match the declared login",
            command=login_command,
        )

    permission_command = [real_gh, "api", f"repos/{target}", "--jq", ".permissions.push"]
    permission = run(permission_command, env=verification_environment)
    if isinstance(permission, Exception):
        return verification_failure(real_gh, "push", f"repos/{target}", verification_environment,
                                    permission_command, str(permission))
    if permission.returncode != 0:
        return verification_failure(
            real_gh, "push", f"repos/{target}", verification_environment, permission_command,
            f"GitHub CLI exited with status {permission.returncode}",
        )
    if permission.stdout.splitlines() != ["true"]:
        return fail(
            "github-cli-user-push-permission-missing",
            "GitHub-reported target push permission was not exactly true",
            command=permission_command,
        )
    return 0


def emit_github_cli_user_credential(token: str, expected: str, target: str) -> int:
    json.dump({"token": token, "login": expected, "target": target,
               "identity_proof": "login-verified;token-scope-account-wide-not-repository-scoped"},
              sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


def main() -> int:
    expected = os.environ.get("FKST_GITHUB_BOT_LOGIN", "")
    target = os.environ.get("FKST_GITHUB_REPO", "")
    resolver = os.environ.get("FKST_GITHUB_CREDENTIAL_RESOLVER", "")
    real_gh = os.environ.get("FKST_GITHUB_REAL_GH", "")
    source = os.environ.get("FKST_GITHUB_CREDENTIAL_SOURCE", "")
    if not expected:
        return fail("declared-bot-login-missing", "FKST_GITHUB_BOT_LOGIN is empty")
    if not target or target.count("/") != 1:
        return fail("declared-target-invalid", f"FKST_GITHUB_REPO is not owner/repository: {target!r}")
    if source not in {"github-app", "github-cli-user"}:
        return fail("credential-source-unsupported", f"FKST_GITHUB_CREDENTIAL_SOURCE is {source!r}")

    if source == "github-app":
        for executable, cause in ((resolver, "github-app-resolver-unavailable"),
                                  (real_gh, "github-cli-unavailable")):
            if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
                return fail(cause, f"executable is unavailable: {executable!r}")

        # Never let an inherited credential select the resolver's pass-through branch.
        mint_command = [resolver, "token", "--target", target]
        minted = run(mint_command, env=clean_environment())
        if isinstance(minted, Exception):
            return fail("github-app-token-mint-failed", str(minted), command=mint_command)
        if minted.returncode != 0:
            return fail("github-app-token-mint-failed", minted.stderr, command=mint_command)
        if not minted.stdout.strip():
            return fail("github-app-token-mint-failed", "credential resolver returned empty stdout",
                        command=mint_command)
        token = minted.stdout.strip()
        if "\n" in token or "\r" in token:
            return fail("token-result-invalid", "credential resolver returned more than one line",
                        command=mint_command)

        verification_environment = clean_environment()
        verification_environment["GH_TOKEN"] = token
        repository_command = [real_gh, "api", "--paginate", "/installation/repositories",
                              "--jq", ".repositories[].full_name"]
        repositories = run(repository_command, env=verification_environment)
        if isinstance(repositories, Exception):
            return fail("github-app-installation-repositories-verification-unavailable", str(repositories),
                        command=repository_command)
        if repositories.returncode != 0:
            return fail("github-app-installation-repositories-verification-unavailable",
                        repositories.stderr,
                        command=repository_command)
        accessible = set(repositories.stdout.splitlines())
        if target not in accessible:
            return fail("declared-target-not-accessible-to-installation",
                        f"declared target {target!r} is absent from the installation repositories",
                        command=repository_command)

        # Installation tokens do not resolve through /user, so the bot login cannot
        # be mechanically derived from this token. The resolver source and target
        # access are verified; login remains a declaration checked by the caller.
        json.dump({"token": token, "login": expected, "target": target,
                   "identity_proof": "target-access-only;bot-login-not-mechanically-proven"},
                  sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0

    if not real_gh or not os.path.isfile(real_gh) or not os.access(real_gh, os.X_OK):
        return fail("github-cli-unavailable", f"executable is unavailable: {real_gh!r}")

    mint_command = [real_gh, "auth", "token", "--hostname", "github.com", "--user", expected]
    minted = run(mint_command, env=clean_environment())
    if isinstance(minted, Exception):
        return fail("github-cli-user-token-mint-failed", str(minted), command=mint_command)
    if minted.returncode != 0:
        return fail(
            "github-cli-user-token-mint-failed",
            f"GitHub CLI exited with status {minted.returncode}",
            command=mint_command,
        )
    if not minted.stdout.strip():
        return fail("github-cli-user-token-mint-failed", "GitHub CLI returned empty stdout",
                    command=mint_command)
    token = minted.stdout.strip()
    if "\n" in token or "\r" in token:
        return fail("token-result-invalid", "GitHub CLI returned more than one line",
                    command=mint_command)

    token_fingerprint = hashlib.sha256(token.encode()).hexdigest()
    source = "github-cli-user"
    force_fresh = sys.argv[1:] == ["--fkst-auth-check"]
    if force_fresh:
        status = verify_github_cli_user(real_gh, expected, target, token)
        if status != 0:
            return status
        return emit_github_cli_user_credential(token, expected, target)

    # Generation paths normally differ between launches, but path freshness is not a
    # safety requirement. The boot-session binding prevents attestation reuse across
    # boots even when a generation path is retained or reused.
    boot_session_id = current_boot_session_id()
    monotonic_now = current_monotonic_time()
    paths = attestation_paths() if boot_session_id is not None and monotonic_now is not None else None
    if paths is not None and attestation_matches(
        paths[0], source=source, login=expected, target=target,
        token_fingerprint=token_fingerprint, boot_session_id=boot_session_id,
        monotonic_now=monotonic_now,
    ):
        return emit_github_cli_user_credential(token, expected, target)

    lock_descriptor: int | None = None
    if paths is not None:
        try:
            lock_descriptor = acquire_attestation_lock(paths[1])
        except OSError:
            paths = None

    if lock_descriptor is None:
        status = verify_github_cli_user(real_gh, expected, target, token)
        if status != 0:
            return status
        return emit_github_cli_user_credential(token, expected, target)

    status = 0
    try:
        monotonic_now = current_monotonic_time()
        if monotonic_now is None or not attestation_matches(
                paths[0], source=source, login=expected, target=target,
                token_fingerprint=token_fingerprint, boot_session_id=boot_session_id,
                monotonic_now=monotonic_now):
            status = verify_github_cli_user(real_gh, expected, target, token)
            if status == 0:
                verified_monotonic = current_monotonic_time()
                if verified_monotonic is not None:
                    publish_attestation(
                        paths[0], source=source, login=expected, target=target,
                        token_fingerprint=token_fingerprint, boot_session_id=boot_session_id,
                        verified_monotonic=verified_monotonic,
                    )
    finally:
        os.close(lock_descriptor)
    if status != 0:
        return status
    return emit_github_cli_user_credential(token, expected, target)


if __name__ == "__main__":
    raise SystemExit(main())
