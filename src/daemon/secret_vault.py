"""
daemon/secret_vault.py

Secret Vault wrapper — stores and retrieves the daemon's API key from the
OS encrypted vault via ``keyring``.  All public functions return ``Result``.

The fixed service name ``KMS_DAEMON`` and username ``daemon`` are hard
contracts shared with the engine (config.py) and cloud server (api.py).
"""

from __future__ import annotations

import os

import keyring
import keyring.errors

from core.result import Failure, Result, Success

# ── Hard contracts (do not parameterize) ────────────────────────────────
# COUPLING: KMS_DAEMON_API_KEY is the exact env slot read by
# daemon/config.py:147 and mcp_server/api.py:61.
SERVICE_NAME: str = "KMS_DAEMON"
USERNAME: str = "daemon"
ENV_VAR: str = "KMS_DAEMON_API_KEY"


def store_key(key: str) -> Result[None]:
    """Store the daemon API key in the OS encrypted vault.

    Args:
        key: The API key to store.

    Returns:
        Success(None) if stored successfully.
        Failure if keyring raises an exception.
    """
    try:
        keyring.set_password(SERVICE_NAME, USERNAME, key)
    except Exception as exc:
        return Failure(
            error=f"Failed to store API key: {exc}",
            recoverable=False,
            context={"service": SERVICE_NAME},
        )
    return Success(None)


def read_key() -> Result[str]:
    """Read the daemon API key from the OS encrypted vault.

    Returns:
        Success[str] containing the API key if found.
        Failure if the key is absent or keyring raises an exception.
    """
    try:
        key = keyring.get_password(SERVICE_NAME, USERNAME)
    except Exception as exc:
        return Failure(
            error=f"Failed to read API key: {exc}",
            recoverable=False,
            context={"service": SERVICE_NAME},
        )
    if key is None:
        return Failure(
            error="API key not found in OS vault",
            recoverable=False,
            context={"service": SERVICE_NAME},
        )
    return Success(key)


def load_key_into_env() -> Result[None]:
    """Read the key from the vault and set it in the environment.

    Sets ``os.environ["KMS_DAEMON_API_KEY"]`` — the exact slot read by
    ``load_daemon_config`` (daemon/config.py:147).

    RULE: sets the env slot DIRECTLY via os.environ — never use dotenv (C-11).

    Returns:
        Success(None) if the key was read and set.
        Failure if the key could not be read.
    """
    match read_key():
        case Success(key):
            os.environ[ENV_VAR] = key
            return Success(None)
        case Failure() as f:
            return f


def delete_key() -> Result[None]:
    """Delete the daemon API key from the OS encrypted vault.

    Used by Phase 6 uninstall cleanup.  Idempotent — no error if the
    key was already absent.

    Returns:
        Success(None) if the key was deleted or was already absent.
        Failure if keyring raises an exception.
    """
    try:
        keyring.delete_password(SERVICE_NAME, USERNAME)
    except keyring.errors.PasswordDeleteError:
        return Success(None)  # already absent — idempotent
    except Exception as exc:
        return Failure(
            error=f"Failed to delete API key: {exc}",
            recoverable=False,
            context={"service": SERVICE_NAME},
        )
    return Success(None)
