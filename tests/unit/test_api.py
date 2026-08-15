"""Unit tests for the FastAPI application (Phase 1 health endpoints)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_endpoint(fresh_db) -> None:
    from app.api import create_app

    app = create_app()
    client = TestClient(app)
    with client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "api_keys" in body


def test_mcp_tools_endpoint(fresh_db) -> None:
    from app.api import create_app

    app = create_app()
    client = TestClient(app)
    with client:
        resp = client.get("/mcp/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert "servers" in body
    assert "script" in body["servers"]
