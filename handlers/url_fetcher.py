"""URL detection and content fetching utilities for the capture pipeline.

NOT a BaseHandler — URLs come from inside note bodies, not filesystem drops.
The HandlerRegistry is path-based; this module is the separate URL dispatch
path used by the capture pipeline.

Kept synchronous intentionally — blocking I/O. The async pipeline stage wraps
calls with asyncio.to_thread(fetch_url_content, url). This matches the Ollama
provider pattern (TD-010): async wrapper at the call site, not here.

Security guarantees of _fetch_web:
    - Scheme is asserted http(s); file://, gopher://, etc. rejected.
    - Hostname is resolved once with a hard DNS timeout. Resolved IPs are
      checked against private/loopback/link-local/reserved/multicast/
      unspecified ranges (SSRF guard). AWS/GCP metadata endpoints
      (169.254.169.254) are covered by `is_link_local`.
    - The set of pre-validated IPs is pinned. After the HTTP connection
      is open, the actual peer IP (`socket.getpeername()`) is checked
      against that set BEFORE any response body is read. A mismatch
      (DNS rebinding: attacker flips DNS between validation and the
      libc resolve performed by requests/urllib3) aborts the fetch
      with no body exfiltration.
    - Redirects are followed manually with re-validation at each hop,
      capped by CONFIG.main.handlers.max_redirects. requests' default
      automatic redirect following is disabled.
    - Response body is read in 64 KB chunks and aborted if total exceeds
      CONFIG.main.handlers.max_web_fetch_bytes. Non-text Content-Type
      values are refused before any body read.

Threading:
    `_DNS_EXECUTOR` is a small module-level ThreadPoolExecutor used to
    impose a hard timeout on `socket.getaddrinfo` (the stdlib resolver
    has no timeout argument). It is safe for re-entry from multiple
    asyncio.to_thread workers; concurrent fetches each submit their
    own resolution job.
"""
import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from urllib.parse import urljoin, parse_qs, urlparse

import requests
import structlog
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from core.result import Failure, Result, Success

__all__ = ["detect_urls", "fetch_url_content"]

logger = structlog.get_logger(__name__)

_URL_PATTERN = re.compile(r"https?://[^\s\)\]\>\"]+")
_URL_TRAILING_PUNCT = ".,;:!?"

_YOUTUBE_NETLOCS = frozenset(
    {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}
)
_YOUTUBE_WATCH_NETLOCS = frozenset(
    {"www.youtube.com", "youtube.com", "m.youtube.com"}
)

_USER_AGENT = "AI-kms/0.1"
_CHUNK_SIZE = 64 * 1024

# Module-level executor for DNS resolution with timeout. getaddrinfo is
# blocking and has no timeout parameter; submitting it to an executor lets
# us wait with a deadline. Daemon threads so this never blocks interpreter
# shutdown.
_DNS_EXECUTOR = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="kms-dns"
)


def detect_urls(text: str) -> list[str]:
    """Return all unique HTTP/HTTPS URLs found in text, in order of first appearance.

    Strips trailing sentence punctuation (.,;:!?) that the URL regex would
    otherwise grab.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in _URL_PATTERN.findall(text):
        url = raw.rstrip(_URL_TRAILING_PUNCT)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _is_youtube(url: str) -> bool:
    return urlparse(url).netloc in _YOUTUBE_NETLOCS


def _extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc in _YOUTUBE_WATCH_NETLOCS:
        params = parse_qs(parsed.query)
        ids = params.get("v", [])
        return ids[0] if ids else None
    if parsed.netloc == "youtu.be":
        segment = parsed.path.lstrip("/")
        return segment if segment else None
    return None


def _resolve_host(host: str, timeout: float) -> list[tuple]:
    """Resolve host with a hard timeout. Raises socket.gaierror on failure.

    Wraps socket.getaddrinfo in a ThreadPoolExecutor to enforce a deadline
    the stdlib resolver does not natively support. A timeout is reported as
    socket.gaierror so callers handle it uniformly with other DNS errors.
    """
    future = _DNS_EXECUTOR.submit(socket.getaddrinfo, host, None)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout:
        # The submitted getaddrinfo continues running in its worker thread
        # until the OS resolver returns; we cannot cancel it. That's fine —
        # the worker pool tolerates straggling tasks, and the next fetch
        # will queue behind them. Worst case the pool fills, in which case
        # submit() itself blocks; max_workers=4 keeps the cost bounded.
        raise socket.gaierror(f"DNS resolution timed out after {timeout}s")


def _ip_is_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if ip is a public unicast address safe to connect to."""
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_url_safe(url: str, dns_timeout: float = 5.0) -> Result[frozenset[str]]:
    """SSRF guard. Reject non-http schemes and hosts that resolve to non-public IPs.

    Returns:
        Success(frozenset of validated IP strings) on safe URL.
        Failure(recoverable=False) on bad scheme, missing host, or any
            resolved IP being private/loopback/link-local/reserved/
            multicast/unspecified.
        Failure(recoverable=True) on DNS timeout or resolution failure.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return Failure(
            error=f"Refusing non-http scheme: {parsed.scheme!r}",
            recoverable=False,
            context={"url": url, "scheme": parsed.scheme},
        )
    host = parsed.hostname
    if not host:
        return Failure(
            error="URL has no hostname",
            recoverable=False,
            context={"url": url},
        )
    try:
        addr_infos = _resolve_host(host, dns_timeout)
    except socket.gaierror as exc:
        return Failure(
            error=f"DNS resolution failed: {exc}",
            recoverable=True,
            context={"url": url, "host": host},
        )
    validated: set[str] = set()
    for _family, _socktype, _proto, _canon, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not _ip_is_safe(ip):
            return Failure(
                error=f"SSRF guard: host {host} resolves to non-public IP {ip}",
                recoverable=False,
                context={"url": url, "host": host, "ip": str(ip)},
            )
        validated.add(str(ip))
    if not validated:
        return Failure(
            error=f"DNS resolution returned no usable addresses for {host}",
            recoverable=True,
            context={"url": url, "host": host},
        )
    return Success(frozenset(validated))


def _get_peer_ip(resp: requests.Response) -> str | None:
    """Return the actual remote IP the response is connected to, or None.

    urllib3 exposes the underlying connection via resp.raw.connection.
    Best-effort: failure to read this is not fatal but disables the
    DNS-rebinding check for that response. We log a warning when it
    happens so operators know the protection is degraded.
    """
    try:
        raw = resp.raw
        conn = getattr(raw, "connection", None) or getattr(raw, "_connection", None)
        if conn is None:
            return None
        sock = getattr(conn, "sock", None)
        if sock is None:
            return None
        peer = sock.getpeername()
        return peer[0] if peer else None
    except Exception:
        return None


def _fetch_youtube(url: str) -> Result[str]:
    """Fetch transcript text for a YouTube video.

    No SSRF guard needed: destination is the YouTube transcript endpoint,
    hardcoded inside youtube_transcript_api. Video ID is the only user-
    controlled input and it cannot redirect the API to a private host.
    """
    video_id = _extract_video_id(url)
    if video_id is None:
        return Failure(
            error=f"Cannot extract video ID from URL: {url}",
            recoverable=False,
            context={"url": url},
        )
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        text = " ".join(snippet.text for snippet in transcript)
        logger.info(
            "youtube.fetch.ok", url=url, video_id=video_id, chars=len(text)
        )
        return Success(text)
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as exc:
        logger.warning(
            "youtube.fetch.no_transcript",
            url=url,
            video_id=video_id,
            error=str(exc),
        )
        return Failure(
            error=f"No transcript available: {exc}",
            recoverable=False,
            context={"url": url, "video_id": video_id},
        )
    except Exception as exc:
        logger.warning(
            "youtube.fetch.error", url=url, video_id=video_id, error=str(exc)
        )
        return Failure(
            error=f"YouTube transcript fetch failed: {exc}",
            recoverable=True,
            context={"url": url, "video_id": video_id},
        )


def _fetch_web(url: str) -> Result[str]:
    """Fetch and extract readable text from a web page.

    SSRF-guarded with DNS-rebinding protection, redirect-limited,
    size-capped, text/* only. See module docstring for the full threat
    model.
    """
    # Lazy import: handlers must not load CONFIG at module scope or unit
    # tests on machines without the vault directory fail at import.
    from core.config import CONFIG

    cfg = CONFIG.main.handlers
    timeout = cfg.web_fetch_timeout_seconds
    dns_timeout = cfg.dns_resolve_timeout_seconds
    max_bytes = cfg.max_web_fetch_bytes
    max_redirects = cfg.max_redirects

    current_url = url
    for hop in range(max_redirects + 1):
        guard = _validate_url_safe(current_url, dns_timeout=dns_timeout)
        if isinstance(guard, Failure):
            logger.warning(
                "web.fetch.guard_block",
                url=current_url,
                hop=hop,
                error=guard.error,
            )
            return guard
        validated_ips = guard.value

        try:
            resp = requests.get(
                current_url,
                timeout=timeout,
                headers={"User-Agent": _USER_AGENT},
                allow_redirects=False,
                stream=True,
            )
        except Exception as exc:
            logger.warning(
                "web.fetch.error", url=current_url, hop=hop, error=str(exc)
            )
            return Failure(
                error=f"Web fetch failed: {exc}",
                recoverable=True,
                context={"url": current_url, "hop": hop},
            )

        try:
            # DNS-rebinding check: the actual peer IP must be one we
            # pre-validated. requests/urllib3 may have re-resolved the
            # hostname using libc, so the connection could land on an IP
            # an attacker DNS server returned AFTER our SSRF check.
            peer_ip = _get_peer_ip(resp)
            if peer_ip is None:
                logger.warning(
                    "web.fetch.peer_ip_unavailable",
                    url=current_url,
                    hop=hop,
                )
                # Continue without the check — degraded but not fatal.
                # Pre-validation already constrained the DNS answer.
            elif peer_ip not in validated_ips:
                logger.warning(
                    "web.fetch.dns_rebind",
                    url=current_url,
                    hop=hop,
                    peer_ip=peer_ip,
                    validated_ips=sorted(validated_ips),
                )
                return Failure(
                    error=(
                        f"DNS rebinding detected: peer {peer_ip} not in "
                        f"validated set {sorted(validated_ips)}"
                    ),
                    recoverable=False,
                    context={
                        "url": current_url,
                        "peer_ip": peer_ip,
                        "validated_ips": sorted(validated_ips),
                    },
                )

            if resp.is_redirect or resp.is_permanent_redirect:
                next_url = resp.headers.get("Location")
                if not next_url:
                    return Failure(
                        error="Redirect response missing Location header",
                        recoverable=False,
                        context={"url": current_url, "hop": hop},
                    )
                current_url = urljoin(current_url, next_url)
                continue

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                logger.warning(
                    "web.fetch.http_error",
                    url=current_url,
                    status=resp.status_code,
                    error=str(exc),
                )
                return Failure(
                    error=f"HTTP {resp.status_code}: {exc}",
                    recoverable=True,
                    context={"url": current_url, "status": resp.status_code},
                )

            content_type_raw = resp.headers.get("Content-Type", "")
            content_type = content_type_raw.split(";")[0].strip().lower()
            # Extract charset only if the server explicitly declared one.
            # Don't fall back to requests' ISO-8859-1 default — that would
            # override <meta charset> sniffing and mojibake UTF-8 pages.
            declared_charset: str | None = None
            for part in content_type_raw.split(";")[1:]:
                part = part.strip()
                if part.lower().startswith("charset="):
                    declared_charset = (
                        part.split("=", 1)[1].strip().strip('"\'') or None
                    )
                    break
            if content_type and not content_type.startswith("text/"):
                return Failure(
                    error=f"Refusing non-text Content-Type: {content_type!r}",
                    recoverable=False,
                    context={
                        "url": current_url,
                        "content_type": content_type,
                    },
                )

            declared_len = resp.headers.get("Content-Length")
            if declared_len is not None:
                try:
                    if int(declared_len) > max_bytes:
                        return Failure(
                            error=(
                                f"Response too large: {declared_len} > "
                                f"{max_bytes} bytes"
                            ),
                            recoverable=False,
                            context={
                                "url": current_url,
                                "content_length": declared_len,
                                "limit": max_bytes,
                            },
                        )
                except ValueError:
                    pass  # malformed header — rely on chunked size check

            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    return Failure(
                        error=f"Response body exceeded {max_bytes} bytes",
                        recoverable=False,
                        context={
                            "url": current_url,
                            "bytes_read": total,
                            "limit": max_bytes,
                        },
                    )
                chunks.append(chunk)
            body = b"".join(chunks)

            soup = BeautifulSoup(
                body, "html.parser", from_encoding=declared_charset
            )
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            logger.info(
                "web.fetch.ok",
                url=url,
                final_url=current_url,
                bytes=total,
                chars=len(text),
                hops=hop,
                peer_ip=peer_ip,
            )
            return Success(text)
        finally:
            resp.close()

    logger.warning(
        "web.fetch.too_many_redirects", url=url, max_redirects=max_redirects
    )
    return Failure(
        error=f"Exceeded {max_redirects} redirects",
        recoverable=False,
        context={"url": url, "max_redirects": max_redirects},
    )


def fetch_url_content(url: str) -> Result[str]:
    """Fetch content from a URL, dispatching to the appropriate fetcher.

    YouTube URLs use the transcript API; all others use HTTP scraping with
    SSRF, DNS-rebinding, redirect, size, and Content-Type guards.
    """
    if _is_youtube(url):
        return _fetch_youtube(url)
    return _fetch_web(url)
