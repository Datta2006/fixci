"""Smoke test for the demo app."""

from __future__ import annotations

from app import app


def test_health_endpoint() -> None:
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
