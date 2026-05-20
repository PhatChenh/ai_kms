"""Shared fixtures for handlers tests.

`stub_config` is autouse so every handler test gets a sane HandlersConfig
without requiring the real vault directory to exist on the machine running
the tests. Mirrors the pattern in tests/test_vault/conftest.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.config import HandlersConfig
from handlers.registry import HandlerRegistry


@pytest.fixture(autouse=True)
def stub_config(monkeypatch):
    """Replace core.config._CONFIG with a stub exposing main.handlers.

    Handlers lazy-import CONFIG inside extract() / _fetch_web(); without this
    fixture every PDF/DOCX/url_fetcher test would try to load the real
    config.yaml and fail on machines that don't have the vault directory.
    """
    import core.config as cfg_module

    fake = MagicMock()
    fake.main.handlers = HandlersConfig()
    monkeypatch.setattr(cfg_module, "_CONFIG", fake)
    yield


@pytest.fixture
def clean_registry():
    """Save and restore HandlerRegistry._handlers around each test.

    Prevents dummy handlers registered in one test from leaking into others.
    All tests that call HandlerRegistry.register must use this fixture.
    """
    saved = HandlerRegistry._handlers[:]
    yield
    HandlerRegistry._handlers[:] = saved
