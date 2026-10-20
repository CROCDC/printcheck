"""Fixtures for the suite. Shared helpers live in helpers.py, importable
because this directory goes on sys.path here: pytest runs in importlib mode
(the repository root is itself the package, under any folder name), where
test modules cannot import each other or conftest by name."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from printcheck.preflight import RULES, Report  # noqa: E402


@pytest.fixture
def rep():
    return Report()


@pytest.fixture
def rules():
    return dict(RULES)
