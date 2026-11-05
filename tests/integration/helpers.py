"""Helpers shared by the integration tests."""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the Alembic CLI exactly as a developer would, against the test database."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
