"""Tests that expose the off-by-one bug in ``calc.add``."""

from __future__ import annotations

from calc import add, multiply


def test_add() -> None:
    assert add(2, 2) == 4


def test_add_negative() -> None:
    assert add(-1, 1) == 0


def test_multiply() -> None:
    assert multiply(3, 4) == 12
