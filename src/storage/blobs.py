"""
storage/blobs.py

Blob Store module — abstract interface + two adapters (local filesystem for
tests, S3 for production).  All public methods return Result[Success|Failure];
exceptions are caught and wrapped in Failure.

Callers depend on the BlobStore interface, never on a concrete adapter.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from core.result import Failure, Result, Success


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class BlobStore(ABC):
    """Protocol for writing, reading, checking, and deleting blobs.

    Every public method returns a Result — never raises.
    """

    @abstractmethod
    def put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        """Store data under key.  Returns Success(None) or Failure."""
        ...

    @abstractmethod
    def get(self, key: str) -> Result[bytes]:
        """Retrieve the blob stored at key.  Returns Success(bytes) or Failure."""
        ...

    @abstractmethod
    def delete(self, key: str) -> Result[None]:
        """Remove the blob at key.  Missing key is NOT an error — returns Success."""
        ...

    @abstractmethod
    def exists(self, key: str) -> Result[bool]:
        """Check whether key exists.  Returns Success(True/False) or Failure."""
        ...


# ---------------------------------------------------------------------------
# Local filesystem adapter — for tests
# ---------------------------------------------------------------------------


class LocalBlobStore(BlobStore):
    """Stores blobs as plain files under <root>/blobs/<key>.

    Intended for testing only.  Not safe for concurrent access across
    processes — there are no file locks.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _path_for(self, key: str) -> Path:
        return self._root / "blobs" / key

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    def put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        try:
            p = self._path_for(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            return Success(None)
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "put"},
            )

    def get(self, key: str) -> Result[bytes]:
        try:
            data = self._path_for(key).read_bytes()
            return Success(data)
        except FileNotFoundError:
            return Failure(
                error=f"blob not found: {key}",
                recoverable=False,
                context={"key": key, "operation": "get"},
            )
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "get"},
            )

    def delete(self, key: str) -> Result[None]:
        try:
            p = self._path_for(key)
            if p.exists():
                p.unlink()
            # Missing key is not an error — the caller wanted it gone,
            # and it is gone.
            return Success(None)
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "delete"},
            )

    def exists(self, key: str) -> Result[bool]:
        try:
            return Success(self._path_for(key).exists())
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "exists"},
            )


# ---------------------------------------------------------------------------
# S3 adapter — production
# ---------------------------------------------------------------------------


class S3BlobStore(BlobStore):
    """Stores blobs in an S3-compatible object store via boto3.

    The constructor takes plain credentials; the caller is responsible for
    reading them from environment variables.

    Async wrappers (async_put, async_get, …) use asyncio.to_thread so they
    can be awaited from async pipelines without blocking the event loop.
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        import boto3  # lazy import so LocalBlobStore users don't need it

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    # ------------------------------------------------------------------
    # sync interface
    # ------------------------------------------------------------------

    def put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=mime_type,
            )
            return Success(None)
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "put"},
            )

    def get(self, key: str) -> Result[bytes]:
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=key,
            )
            body = response["Body"].read()
            return Success(body)
        except self._client.exceptions.NoSuchKey:
            return Failure(
                error=f"blob not found: {key}",
                recoverable=False,
                context={"key": key, "operation": "get"},
            )
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "get"},
            )

    def delete(self, key: str) -> Result[None]:
        try:
            self._client.delete_object(
                Bucket=self._bucket,
                Key=key,
            )
            return Success(None)
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "delete"},
            )

    def exists(self, key: str) -> Result[bool]:
        try:
            self._client.head_object(
                Bucket=self._bucket,
                Key=key,
            )
            return Success(True)
        except self._client.exceptions.ClientError:
            # Typically a 404 — key not found
            return Success(False)
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "exists"},
            )

    # ------------------------------------------------------------------
    # async wrappers — for use from async pipelines
    # ------------------------------------------------------------------

    async def async_put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        return await asyncio.to_thread(self.put, key, data, mime_type)

    async def async_get(self, key: str) -> Result[bytes]:
        return await asyncio.to_thread(self.get, key)

    async def async_delete(self, key: str) -> Result[None]:
        return await asyncio.to_thread(self.delete, key)

    async def async_exists(self, key: str) -> Result[bool]:
        return await asyncio.to_thread(self.exists, key)
