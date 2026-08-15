"""Pytest configuration and shared fixtures.

Each test run uses an isolated in-memory SQLite database and a temporary data
directory so tests never touch real project files.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Force a clean, in-memory SQLite database and a temp data dir for every test
# process BEFORE app.config is imported.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="ava_test_"))
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DIR}/test.db")
os.environ.setdefault("DATA_DIR", str(_TMP_DIR))
os.environ.setdefault("LOGS_DIR", str(_TMP_DIR / "logs"))
os.environ.setdefault("MAP_PROVIDER", "none")
os.environ.setdefault("LLM_PROVIDER", "none")


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp() -> Any:
    yield
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture()
def fresh_db() -> Any:
    """Reset the DB engine and (re)create all tables for an isolated test."""
    from app.database import init_db, reset_engine

    reset_engine()
    init_db()
    yield
    reset_engine()


@pytest.fixture()
def guardrails() -> Any:
    from app.guardrails.guardrails import Guardrails

    return Guardrails()


@pytest.fixture()
def mcp_client() -> Any:
    from app.mcp.client import McpClient

    return McpClient()
