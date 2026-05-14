"""
tests/test_llm/conftest.py

Load .env once for the test session so integration tests can read API keys
without going through cli/main.py. Unit tests use monkeypatch instead.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env", override=False)
