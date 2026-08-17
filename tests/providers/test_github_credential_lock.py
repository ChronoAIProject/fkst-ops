"""Deterministic checks for the credential attestation peer lock."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / "providers" / "github_credential_gh.py"


def load_provider():
    spec = importlib.util.spec_from_file_location("github_credential_lock_under_test", PROVIDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROVIDER_MODULE = load_provider()


def test_github_cli_user_peer_lock_wait_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        lock_path = Path(directory) / "peer.lock"
        clock = [10.0]
        sleeps: list[float] = []

        def sleep(duration: float) -> None:
            sleeps.append(duration)
            clock[0] += duration

        with mock.patch.object(
            PROVIDER_MODULE.fcntl, "flock", side_effect=BlockingIOError
        ), mock.patch.object(
            PROVIDER_MODULE.time, "monotonic", side_effect=lambda: clock[0]
        ), mock.patch.object(PROVIDER_MODULE.time, "sleep", side_effect=sleep):
            descriptor = PROVIDER_MODULE.acquire_attestation_lock(lock_path)

        assert descriptor is None
        assert PROVIDER_MODULE.ATTESTATION_LOCK_WAIT_SECONDS == 0.25
        assert round(sum(sleeps), 9) == PROVIDER_MODULE.ATTESTATION_LOCK_WAIT_SECONDS
        assert all(
            duration <= PROVIDER_MODULE.ATTESTATION_LOCK_RETRY_SECONDS
            for duration in sleeps
        )
