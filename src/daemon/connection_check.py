"""
daemon/connection_check.py

Cloud Connection Check — proves a key + cloud address actually work
before the Wizard lets the user finish.

Targets the GATED endpoint /api/state (NOT the open /health ping).
"""

from __future__ import annotations

import httpx

from core.result import Failure, Result, Success


async def check_connection(
    endpoint: str,
    key: str,
    client: httpx.AsyncClient | None = None,
) -> Result[None]:
    """Check connectivity to the cloud endpoint with an API key.

    Sends GET {endpoint}/api/state with Authorization: Bearer {key}.
    Returns Success(None) on 200, or Failure describing the error.

    Args:
        endpoint: Base URL of the cloud server (e.g. "http://localhost:8080").
        key: API key to authenticate with.
        client: Optional httpx.AsyncClient (injected for testing).
    """
    url = f"{endpoint}/api/state"

    async def _request(cl: httpx.AsyncClient) -> Result[None]:
        try:
            resp = await cl.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                return Failure(
                    error=f"authentication failed: {exc.response.text[:200]}",
                    recoverable=False,
                    context={"status_code": 401, "endpoint": endpoint},
                )
            return Failure(
                error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                recoverable=False,
                context={"status_code": exc.response.status_code, "endpoint": endpoint},
            )
        except httpx.RequestError as exc:
            return Failure(
                error=f"cannot reach cloud endpoint: {exc}",
                recoverable=True,
                context={"endpoint": endpoint},
            )
        else:
            return Success(None)

    if client is not None:
        return await _request(client)
    else:
        async with httpx.AsyncClient(timeout=10) as new_client:
            return await _request(new_client)
