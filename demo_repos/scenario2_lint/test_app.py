"""Tests that pass; the failure in this demo is the lint step."""

from __future__ import annotations

from app import greet


def test_greet() -> None:
    assert greet("world") == "Hello, world!"
