"""Test that explicit dependencies are resolvable (C2-7)."""


def test_uvicorn_is_importable():
    """Prove uvicorn is an explicit dependency — import must succeed."""
    import uvicorn  # noqa: F401
