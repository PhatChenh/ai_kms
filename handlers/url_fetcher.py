"""URL detection and content fetching utilities for the capture pipeline.

Kept synchronous intentionally — blocking I/O. The async pipeline stage wraps
calls with asyncio.to_thread(fetch_url_content, url). This matches the Ollama
provider pattern (TD-010): async wrapper at the call site, not here.
"""
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from core.result import Failure, Result, Success

__all__ = ["detect_urls", "fetch_url_content"]

_URL_PATTERN = re.compile(r"https?://[^\s\)\]\>\"]+")

_YOUTUBE_NETLOCS = frozenset({"www.youtube.com", "youtube.com", "youtu.be"})


def detect_urls(text: str) -> list[str]:
    """Return all unique HTTP/HTTPS URLs found in text, in order of first appearance.

    Args:
        text: Arbitrary text, including Markdown.

    Returns:
        Deduplicated list of URL strings, preserving first-occurrence order.
    """
    seen: set[str] = set()
    result: list[str] = []
    for url in _URL_PATTERN.findall(text):
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _is_youtube(url: str) -> bool:
    """Return True if url is a YouTube watch or short URL.

    Args:
        url: Absolute URL string.

    Returns:
        True for youtube.com and youtu.be domains.
    """
    return urlparse(url).netloc in _YOUTUBE_NETLOCS


def _extract_video_id(url: str) -> str | None:
    """Extract the YouTube video ID from a watch or short URL.

    Args:
        url: A youtube.com or youtu.be URL.

    Returns:
        Video ID string, or None if the URL shape is not recognised.
    """
    parsed = urlparse(url)
    if parsed.netloc in ("www.youtube.com", "youtube.com"):
        params = parse_qs(parsed.query)
        ids = params.get("v", [])
        return ids[0] if ids else None
    if parsed.netloc == "youtu.be":
        # Path is "/<video_id>"; strip leading slash.
        segment = parsed.path.lstrip("/")
        return segment if segment else None
    return None


def _fetch_youtube(url: str) -> Result[str]:
    """Fetch transcript text for a YouTube video.

    Args:
        url: A youtube.com or youtu.be URL.

    Returns:
        Success(text) with joined transcript snippets.
        Failure(recoverable=False) if the video has no transcript.
        Failure(recoverable=True) on network / API errors.
    """
    video_id = _extract_video_id(url)
    if video_id is None:
        return Failure(
            error=f"Cannot extract video ID from URL: {url}",
            recoverable=False,
            context={"url": url},
        )
    try:
        # v1.x API: instantiate the class, call instance method .fetch()
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        text = " ".join(snippet.text for snippet in transcript)
        return Success(text)
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as exc:
        return Failure(
            error=f"No transcript available: {exc}",
            recoverable=False,
            context={"url": url, "video_id": video_id},
        )
    except Exception as exc:
        return Failure(
            error=f"YouTube transcript fetch failed: {exc}",
            recoverable=True,
            context={"url": url, "video_id": video_id},
        )


def _fetch_web(url: str) -> Result[str]:
    """Fetch and extract readable text from a web page.

    Removes script and style tags before extracting text.

    Args:
        url: HTTP/HTTPS URL.

    Returns:
        Success(text) with plain-text page content.
        Failure(recoverable=True) on network or HTTP errors.
    """
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return Success(text)
    except Exception as exc:
        return Failure(
            error=f"Web fetch failed: {exc}",
            recoverable=True,
            context={"url": url},
        )


def fetch_url_content(url: str) -> Result[str]:
    """Fetch content from a URL, dispatching to the appropriate fetcher.

    YouTube URLs use the transcript API; all others use HTTP scraping.

    Args:
        url: Absolute HTTP/HTTPS URL.

    Returns:
        Success(text) on successful extraction.
        Failure(recoverable=False) if content structurally unavailable (e.g. no transcript).
        Failure(recoverable=True) on transient network errors.
    """
    if _is_youtube(url):
        return _fetch_youtube(url)
    return _fetch_web(url)
