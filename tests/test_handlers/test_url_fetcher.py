"""Tests for handlers/url_fetcher.py — URL detection and content fetching."""
import pytest

from handlers.url_fetcher import (
    _extract_video_id,
    _is_youtube,
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


def test_extract_video_id_malformed():
    assert _extract_video_id("https://youtube.com/channel/UCabc") is None


def test_extract_video_id_no_v_param():
    assert _extract_video_id("https://www.youtube.com/watch") is None


# ---------------------------------------------------------------------------
# fetch_url_content — error path only (no real network in unit tests)
# ---------------------------------------------------------------------------


def test_fetch_url_content_unreachable_url_returns_recoverable_failure():
    # Non-routable address forces connection error — no real network needed.
    result = fetch_url_content("http://127.0.0.1:19999/nonexistent")
    assert isinstance(result, Failure)
    assert result.recoverable is True


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
