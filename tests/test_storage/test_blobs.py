"""Tests for BlobStore module — Phase 2 of Phase 7B Visual/Binary Capture."""

from __future__ import annotations

import pytest

from core.result import Failure, Success
from storage.blobs import LocalBlobStore


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-01: put then get returns same bytes (tracer bullet)
# ---------------------------------------------------------------------------


def test_put_then_get_returns_same_bytes(tmp_path):
    """put() followed by get() returns the exact same bytes."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/hello.bin"
    data = b"hello world blob"

    result_put = store.put(key, data, mime_type="application/octet-stream")
    assert result_put.is_success()

    result_get = store.get(key)
    assert result_get.is_success()
    assert result_get.value == data


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-01b: get on non-existent key returns Failure
# ---------------------------------------------------------------------------


def test_get_nonexistent_key_returns_failure(tmp_path):
    """get() on a key that was never written returns a Failure."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/nonexistent.bin"

    result = store.get(key)
    assert result.is_failure()
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-02: put same key twice is idempotent
# ---------------------------------------------------------------------------


def test_put_same_key_twice_is_idempotent(tmp_path):
    """Putting the same key twice does not error — it overwrites silently."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/idempotent.bin"

    result1 = store.put(key, b"first", mime_type="application/octet-stream")
    assert result1.is_success()

    result2 = store.put(key, b"second", mime_type="application/octet-stream")
    assert result2.is_success()

    # The stored content should be the second write
    result_get = store.get(key)
    assert result_get.is_success()
    assert result_get.value == b"second"


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-03: delete removes the object, exists returns False
# ---------------------------------------------------------------------------


def test_delete_removes_object_exists_returns_false(tmp_path):
    """After delete, exists() returns Success(False)."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/to-delete.bin"

    store.put(key, b"data", mime_type="application/octet-stream")

    # Before delete: exists is True
    result_exists = store.exists(key)
    assert result_exists.is_success()
    assert result_exists.value is True

    # Delete
    result_delete = store.delete(key)
    assert result_delete.is_success()

    # After delete: exists is False
    result_exists2 = store.exists(key)
    assert result_exists2.is_success()
    assert result_exists2.value is False


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-04: delete on a missing key returns Success (not an error)
# ---------------------------------------------------------------------------


def test_delete_missing_key_returns_success(tmp_path):
    """Deleting a key that does not exist is still a Success — not an error."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/never-created.bin"

    result = store.delete(key)
    assert result.is_success()


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-05: exists on a missing key returns Success(False)
# ---------------------------------------------------------------------------


def test_exists_missing_key_returns_success_false(tmp_path):
    """exists() on a missing key returns Success(False), not Failure."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/nonexistent.bin"

    result = store.exists(key)
    assert result.is_success()
    assert result.value is False


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-05b: path traversal is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "/etc/passwd",           # absolute path
        "../etc/passwd",         # parent traversal
        "foo/../../../etc/passwd",  # deep traversal
        "",                       # empty
        "///",                    # separator-only
    ],
)
def test_path_traversal_rejected(tmp_path, key):
    """Keys that escape the blob root are rejected with Failure('invalid key')."""
    store = LocalBlobStore(root=tmp_path)

    for method_name, args in [
        ("put", (key, b"data", "text/plain")),
        ("get", (key,)),
        ("exists", (key,)),
        ("delete", (key,)),
    ]:
        method = getattr(store, method_name)
        result = method(*args)
        assert result.is_failure(), f"{method_name}({key!r}) should fail"
        assert "invalid key" in result.error.lower(), (
            f"{method_name}({key!r}): expected 'invalid key', got {result.error!r}"
        )


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-05c: mime_type round-trips via sidecar
# ---------------------------------------------------------------------------


def test_mime_type_round_trip(tmp_path):
    """put() persists mime_type; get_mime_type() retrieves it."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/mime.bin"
    data = b"mime test"
    mime_type = "image/png"

    r = store.put(key, data, mime_type=mime_type)
    assert r.is_success()

    r = store.get_mime_type(key)
    assert r.is_success()
    assert r.value == mime_type


def test_get_mime_type_unknown_key_returns_none(tmp_path):
    """get_mime_type() on a non-existent key returns Success(None)."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/no-mime.bin"

    r = store.get_mime_type(key)
    assert r.is_success()
    assert r.value is None


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-06: operations return Result type (never raise)
# ---------------------------------------------------------------------------


def test_all_operations_return_result_type(tmp_path):
    """Every public method returns a Result (Success or Failure), never raises."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/result-types.bin"

    # put returns Result
    r = store.put(key, b"x", mime_type="text/plain")
    assert isinstance(r, Success | Failure)

    # get returns Result
    r = store.get(key)
    assert isinstance(r, Success | Failure)

    # exists returns Result
    r = store.exists(key)
    assert isinstance(r, Success | Failure)

    # delete returns Result
    r = store.delete(key)
    assert isinstance(r, Success | Failure)


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-07: S3BlobStore smoke test — skipped without real credentials
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_s3_blob_store_smoke(tmp_path):
    """Smoke test for S3BlobStore — skips if no real S3 credentials are set."""
    import os

    endpoint = os.environ.get("KMS_BLOB_ENDPOINT")
    bucket = os.environ.get("KMS_BLOB_BUCKET")
    access_key = os.environ.get("KMS_BLOB_ACCESS_KEY_ID")
    secret_key = os.environ.get("KMS_BLOB_SECRET_ACCESS_KEY")

    if not all([endpoint, bucket, access_key, secret_key]):
        pytest.skip("S3 credentials not configured — set KMS_BLOB_* env vars to run")

    from storage.blobs import S3BlobStore

    store = S3BlobStore(
        endpoint=endpoint,
        bucket=bucket,
        access_key_id=access_key,
        secret_access_key=secret_key,
    )

    key = "test/smoke-test.bin"
    data = b"smoke test data"

    # put
    r = store.put(key, data, mime_type="application/octet-stream")
    assert r.is_success()

    # exists
    r = store.exists(key)
    assert r.is_success()
    assert r.value is True

    # get
    r = store.get(key)
    assert r.is_success()
    assert r.value == data

    # delete
    r = store.delete(key)
    assert r.is_success()

    # exists after delete
    r = store.exists(key)
    assert r.is_success()
    assert r.value is False


# ---------------------------------------------------------------------------
# P7-CAP-BLOB-08: S3BlobStore async wrappers exist and return coroutines
# ---------------------------------------------------------------------------


def test_s3_blob_store_async_wrappers_exist():
    """S3BlobStore has async_put convenience wrapper (inherited from ABC)."""
    from storage.blobs import S3BlobStore

    # Instantiate with fake credentials — boto3 client creation is lazy,
    # so this won't actually connect.
    store = S3BlobStore(
        endpoint="http://localhost:9000",
        bucket="test-bucket",
        access_key_id="fake",
        secret_access_key="fake",
    )

    # Verify async wrapper exists and is an async function
    import inspect

    for name in ("async_put",):
        method = getattr(store, name, None)
        assert method is not None, f"Missing async wrapper: {name}"
        assert inspect.iscoroutinefunction(method), f"{name} is not a coroutine function"


# ---------------------------------------------------------------------------
# P9-E-03: ABC async_put wrapper delegates to sync put correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_put_delegates_to_sync_put(tmp_path):
    """Calling await store.async_put(...) on a LocalBlobStore writes the
    blob just like the synchronous put() would."""
    store = LocalBlobStore(root=tmp_path)
    key = "test/async-put.bin"
    data = b"async blob content"

    result = await store.async_put(key, data, mime_type="application/octet-stream")
    assert result.is_success()

    # Verify the blob was actually persisted
    exists_result = store.exists(key)
    assert exists_result.is_success()
    assert exists_result.value is True

    # Verify content round-trips
    get_result = store.get(key)
    assert get_result.is_success()
    assert get_result.value == data
