"""Focused checks for content-addressed launch platform snapshots."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
OPERATOR = ROOT / "ops" / "deployment_operator.sh"


def run(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, cwd=cwd, text=True, capture_output=True, check=True
    )


def test_launch_platform_snapshot_is_content_addressed_and_reused() -> None:
    source_text = OPERATOR.read_text(encoding="utf-8")
    assert 'launch_platform="$RUNTIME_ROOT/.platform/$PLATFORM_REVISION"' in source_text

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source"
        revision_file = source / ".control" / "engine-ref"
        revision_file.parent.mkdir(parents=True)
        revision_file.write_text("e" * 40 + "\n", encoding="ascii")
        run("git", "init", "-q", str(source))
        run("git", "-C", str(source), "config", "user.email", "test@example.invalid")
        run("git", "-C", str(source), "config", "user.name", "Test")
        run("git", "-C", str(source), "add", ".")
        run("git", "-C", str(source), "commit", "-qm", "platform")
        platform_revision = run("git", "-C", str(source), "rev-parse", "HEAD").stdout.strip()
        (source / "platform-state").write_text("advanced\n", encoding="ascii")
        run("git", "-C", str(source), "add", ".")
        run("git", "-C", str(source), "commit", "-qm", "advanced platform")
        advanced_revision = run("git", "-C", str(source), "rev-parse", "HEAD").stdout.strip()
        snapshot = root / "runtime" / ".platform" / platform_revision
        marker = snapshot / "reuse-marker"
        command = f'''eval "$(sed -n '/^materialise_launch_platform()/,/^}}/p' "{OPERATOR}")"
assert_engine_pair_at() {{
  [ "$(git -C "$1" rev-parse HEAD)" = "$2" ] &&
    [ "$(git -C "$1" show "HEAD:.control/engine-ref")" = "$3" ]
}}
materialise_launch_platform "$1" "$2" "$3" "$4"
touch "$2/reuse-marker"
materialise_launch_platform "$1" "$2" "$3" "$4"
'''

        completed = subprocess.run(
            ["/bin/bash", "-c", command, "test", str(source), str(snapshot),
             platform_revision, "e" * 40],
            text=True, capture_output=True, check=False,
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert marker.is_file()
        assert run("git", "-C", str(snapshot), "rev-parse", "HEAD").stdout.strip() == platform_revision

        run("git", "-C", str(snapshot), "reset", "--hard", "-q", advanced_revision)
        revalidated = subprocess.run(
            ["/bin/bash", "-c", command, "test", str(source), str(snapshot),
             platform_revision, "e" * 40],
            text=True, capture_output=True, check=False,
        )
        assert revalidated.returncode != 0
        assert "LAUNCH_PLATFORM_SNAPSHOT_MISMATCH" in revalidated.stderr


def test_reclamation_keeps_current_and_live_referenced_snapshots() -> None:
    with tempfile.TemporaryDirectory() as directory:
        runtime = Path(directory) / "runtime"
        snapshots = runtime / ".platform"
        current = snapshots / ("1" * 40)
        live = snapshots / ("2" * 40)
        stale = snapshots / ("3" * 40)
        for path in (current, live, stale):
            path.mkdir(parents=True)
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)", str(live / "packages/pkg")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        command = f'''eval "$(sed -n '/^clean_stale_launch_platforms()/,/^}}/p' "{OPERATOR}")"
RUNTIME_ROOT="$1"
clean_stale_launch_platforms "$2"
'''
        try:
            completed = subprocess.run(
                ["/bin/bash", "-c", command, "test", str(runtime), str(current)],
                env={**os.environ, "PATH": "/usr/bin:/bin"},
                text=True, capture_output=True, check=False,
            )
        finally:
            holder.terminate()
            holder.wait(timeout=5)

        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert current.is_dir()
        assert live.is_dir()
        assert not stale.exists()
