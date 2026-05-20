"""Shared fixtures for handlers tests."""
import pytest

from handlers.registry import HandlerRegistry


@pytest.fixture
def clean_registry():
    """Save and restore HandlerRegistry._handlers around each test.

    Prevents dummy handlers registered in one test from leaking into others.
    All tests that call HandlerRegistry.register must use this fixture.
    """
    saved = HandlerRegistry._handlers[:]
    yield
    HandlerRegistry._handlers[:] = saved
