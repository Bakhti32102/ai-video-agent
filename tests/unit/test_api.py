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


def test_produce_endpoint(fresh_db) -> None:
    """The /produce endpoint runs the full pipeline and returns a result."""
    import os
    from app.api import create_app

    app = create_app()
    client = TestClient(app)
    with client:
        resp = client.post("/produce", json={
            "script_text": "The Gadsden Purchase of 1853 was a landmark treaty between the United States and Mexico.",
            "total_duration_sec": 5.0,
            "project_id": "api_test",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["project_id"] == "api_test"
    assert body["final_state"] in ("completed", "failed")
    assert "render_output" in body
    assert "qa_passed" in body
