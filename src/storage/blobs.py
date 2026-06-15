"""
storage/blobs.py

Blob Store module — abstract interface + two adapters (local filesystem for
tests, S3 for production).  All public methods return Result[Success|Failure];
exceptions are caught and wrapped in Failure.

Callers depend on the BlobStore interface, never on a concrete adapter.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path

from core.result import Failure, Result, Success

_HEX_KEY_RE = re.compile(r"^[a-f0-9]{32,}$")


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

    # ------------------------------------------------------------------
    # async wrappers — for use from async pipelines
    # ------------------------------------------------------------------

    async def async_put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        """Async wrapper around put() — never blocks the event loop."""
        return await asyncio.to_thread(self.put, key, data, mime_type)

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
        # Reject empty or separator-only keys.
        if not key or not key.strip("/"):
            raise ValueError("invalid key")

        blob_root = (self._root / "blobs").resolve()
        full_path = (self._root / "blobs" / key).resolve()

        # Prevent path traversal — the resolved path must stay inside blob_root.
        if not full_path.is_relative_to(blob_root):
            raise ValueError("invalid key")

        return full_path

    # ------------------------------------------------------------------
    # public interface
    # ------------------------------------------------------------------

    def put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        try:
            p = self._path_for(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            # Persist mime_type in a sidecar file so tests can verify it.
            mime_path = p.with_suffix(p.suffix + ".mime")
            mime_path.write_text(mime_type)
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
            # Also remove the mime-type sidecar if present.
            mime_path = p.with_suffix(p.suffix + ".mime")
            if mime_path.exists():
                mime_path.unlink()
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

    def get_mime_type(self, key: str) -> Result[str | None]:
        """Return the stored MIME type for *key*, or None if not recorded.

        This is a *LocalBlobStore*-only helper (not part of the ABC) so tests
        can verify mime-type round-trips end-to-end.
        """
        try:
            p = self._path_for(key)
            mime_path = p.with_suffix(p.suffix + ".mime")
            if mime_path.exists():
                return Success(mime_path.read_text())
            return Success(None)
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "get_mime_type"},
            )


# ---------------------------------------------------------------------------
# S3 adapter — production
# ---------------------------------------------------------------------------


class S3BlobStore(BlobStore):
    """Stores blobs in an S3-compatible object store via boto3.

    The constructor takes plain credentials; the caller is responsible for
    reading them from environment variables.
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

    @staticmethod
    def _validate_key(key: str) -> Result[None]:
        """Validate a blob key is a hex content hash (≥32 hex chars).

        The daemon *should* always send valid SHA-256 hex digests; this is
        defense-in-depth.
        """
        if not _HEX_KEY_RE.match(key):
            return Failure(
                error="invalid blob key",
                recoverable=False,
                context={"key": key},
            )
        return Success(None)

    def put(self, key: str, data: bytes, mime_type: str) -> Result[None]:
        match self._validate_key(key):
            case Failure() as f:
                return f
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
        match self._validate_key(key):
            case Failure() as f:
                return f
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
        match self._validate_key(key):
            case Failure() as f:
                return f
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
        match self._validate_key(key):
            case Failure() as f:
                return f
        try:
            self._client.head_object(
                Bucket=self._bucket,
                Key=key,
            )
            return Success(True)
        except self._client.exceptions.ClientError as exc:
            # Only 404 / NoSuchKey means the key does not exist.
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                return Success(False)
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "exists", "error_code": error_code},
            )
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"key": key, "operation": "exists"},
            )


