"""Module with deliberate ruff violations (unused import + unused variable)."""

from __future__ import annotations

import os


def greet(name: str) -> str:
    """Return a greeting."""
    unused = "this variable is never read"
    return f"Hello, {name}!"
