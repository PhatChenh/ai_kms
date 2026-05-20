"""Tests for handlers/url_fetcher.py — URL detection and content fetching."""
import pytest

from handlers.url_fetcher import (
    _extract_video_id,
    _is_youtube,
    _validate_url_safe,
    detect_urls,
    fetch_url_content,
)
from core.result import Failure, Success


# ---------------------------------------------------------------------------
# detect_urls — pure function, no network
# ---------------------------------------------------------------------------


def test_detect_urls_single_url():
    text = "Check this out: https://example.com for details."
    assert detect_urls(text) == ["https://example.com"]


def test_detect_urls_no_urls():
    assert detect_urls("No links here, just plain text.") == []


def test_detect_urls_multiple_urls():
    text = "See https://example.com and https://example.org for more."
    urls = detect_urls(text)
    assert "https://example.com" in urls
    assert "https://example.org" in urls
    assert len(urls) == 2


def test_detect_urls_deduplicates():
    text = "https://example.com is mentioned here and https://example.com again."
    assert detect_urls(text) == ["https://example.com"]


def test_detect_urls_markdown_link_syntax():
    text = "Read [the docs](https://docs.example.com) now."
    assert detect_urls(text) == ["https://docs.example.com"]


def test_detect_urls_http_and_https():
    text = "http://old.example.com and https://new.example.com"
    urls = detect_urls(text)
    assert "http://old.example.com" in urls
    assert "https://new.example.com" in urls


# ---------------------------------------------------------------------------
# _is_youtube — pure function, no network
# ---------------------------------------------------------------------------


def test_is_youtube_watch_url():
    assert _is_youtube("https://www.youtube.com/watch?v=abc") is True


def test_is_youtube_short_url():
    assert _is_youtube("https://youtu.be/abc") is True


def test_is_youtube_no_www():
    assert _is_youtube("https://youtube.com/watch?v=abc") is True


def test_is_youtube_mobile():
    assert _is_youtube("https://m.youtube.com/watch?v=abc") is True


def test_is_youtube_other_domain():
    assert _is_youtube("https://example.com") is False


def test_is_youtube_vimeo():
    assert _is_youtube("https://vimeo.com/123456") is False


# ---------------------------------------------------------------------------
# _extract_video_id — pure function, no network
# ---------------------------------------------------------------------------


def test_extract_video_id_watch_url():
    assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_short_url():
    assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_mobile_url():
    assert _extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_malformed():
    assert _extract_video_id("https://youtube.com/channel/UCabc") is None


def test_extract_video_id_no_v_param():
    assert _extract_video_id("https://www.youtube.com/watch") is None


# ---------------------------------------------------------------------------
# fetch_url_content — error path only (no real network in unit tests)
# ---------------------------------------------------------------------------


def test_fetch_url_content_loopback_blocked_by_ssrf_guard():
    # 127.0.0.1 is loopback — SSRF guard rejects before any network call.
    result = fetch_url_content("http://127.0.0.1:19999/nonexistent")
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "SSRF" in result.error


def test_fetch_url_content_unresolvable_host_returns_recoverable_failure():
    # .invalid TLD (RFC 6761) is guaranteed not to resolve — DNS failure path.
    result = fetch_url_content("http://nonexistent-host-xyz-12345.invalid/")
    assert isinstance(result, Failure)
    assert result.recoverable is True


# ---------------------------------------------------------------------------
# SSRF guard — _validate_url_safe is pure (DNS only) and exercises the
# rejection branches for private, loopback, link-local, and bad schemes.
# ---------------------------------------------------------------------------


def test_validate_url_safe_rejects_file_scheme():
    result = _validate_url_safe("file:///etc/passwd")
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "scheme" in result.error.lower()


def test_validate_url_safe_rejects_gopher_scheme():
    result = _validate_url_safe("gopher://example.com/")
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_validate_url_safe_rejects_localhost():
    result = _validate_url_safe("http://localhost/admin")
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "SSRF" in result.error


def test_validate_url_safe_rejects_loopback_ip():
    result = _validate_url_safe("http://127.0.0.1/")
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_validate_url_safe_rejects_aws_metadata():
    # 169.254.169.254 is the link-local AWS/GCP/Azure metadata endpoint.
    result = _validate_url_safe("http://169.254.169.254/latest/meta-data/")
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_validate_url_safe_rejects_private_rfc1918():
    result = _validate_url_safe("http://10.0.0.1/")
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_validate_url_safe_rejects_missing_hostname():
    result = _validate_url_safe("http:///path")
    assert isinstance(result, Failure)
    assert result.recoverable is False


def test_validate_url_safe_dns_failure_is_recoverable():
    result = _validate_url_safe("http://nonexistent-host-xyz-12345.invalid/")
    assert isinstance(result, Failure)
    assert result.recoverable is True


def test_validate_url_safe_public_host_returns_ip_set():
    # example.com is a real public host (RFC 2606) — must resolve to public IPs.
    result = _validate_url_safe("https://example.com/")
    # Either Success(frozenset of public IPs) or Failure(recoverable=True) if
    # the test environment has no DNS. Never a SSRF rejection.
    if isinstance(result, Success):
        assert isinstance(result.value, frozenset)
        assert len(result.value) >= 1
        for ip in result.value:
            assert "." in ip or ":" in ip  # v4 or v6
    else:
        assert result.recoverable is True


def test_validate_url_safe_dns_timeout_is_recoverable(monkeypatch):
    # Force the resolver to hang past the timeout; expect a recoverable Failure.
    import handlers.url_fetcher as uf

    def _slow_resolve(host, *args, **kwargs):
        import time
        time.sleep(10)
        return []

    monkeypatch.setattr(uf.socket, "getaddrinfo", _slow_resolve)
    result = _validate_url_safe("http://example.com/", dns_timeout=0.2)
    assert isinstance(result, Failure)
    assert result.recoverable is True
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# DNS rebinding — _fetch_web aborts when peer IP isn't in validated set
# ---------------------------------------------------------------------------


def test_fetch_web_aborts_on_dns_rebind(monkeypatch):
    """Simulate a DNS rebind: _validate_url_safe returns a public IP set, but
    the actual peer socket reports an IP outside that set. _fetch_web must
    abort with Failure(recoverable=False) before any body is read.
    """
    import handlers.url_fetcher as uf
    from core.result import Success as _Success

    monkeypatch.setattr(
        uf,
        "_validate_url_safe",
        lambda url, dns_timeout=5.0: _Success(frozenset({"203.0.113.1"})),
    )

    class _FakeSock:
        def getpeername(self):
            return ("198.51.100.99", 443)  # NOT in validated set

    class _FakeConn:
        sock = _FakeSock()

    class _FakeRaw:
        connection = _FakeConn()

    class _FakeResp:
        raw = _FakeRaw()
        is_redirect = False
        is_permanent_redirect = False
        status_code = 200
        headers = {"Content-Type": "text/html"}

        def close(self):
            pass

        def iter_content(self, chunk_size):
            yield b"<html>secret</html>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(uf.requests, "get", lambda *a, **kw: _FakeResp())

    result = uf._fetch_web("https://attacker.example/")
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert "rebinding" in result.error.lower()


# ---------------------------------------------------------------------------
# detect_urls — trailing punctuation
# ---------------------------------------------------------------------------


def test_detect_urls_strips_trailing_period():
    assert detect_urls("see https://example.com/foo.") == ["https://example.com/foo"]


def test_detect_urls_strips_trailing_comma():
    assert detect_urls("https://a.example, https://b.example") == [
        "https://a.example",
        "https://b.example",
    ]


# ---------------------------------------------------------------------------
# Integration tests — require real network; skipped by default
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_fetch_url_content_web_page_success():
    result = fetch_url_content("https://example.com")
    assert isinstance(result, Success)
    assert len(result.value) > 0


@pytest.mark.integration
def test_fetch_url_content_youtube_transcript_success():
    # Rick Astley — Never Gonna Give You Up has public auto-captions
    result = fetch_url_content("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert isinstance(result, Success)
    assert len(result.value) > 0


@pytest.mark.integration
def test_fetch_url_content_private_youtube_returns_failure():
    # Video ID that has no transcript — expect Failure(recoverable=False)
    result = fetch_url_content("https://www.youtube.com/watch?v=XXXXXXXXXXX")
    assert isinstance(result, Failure)
    assert result.recoverable is False
