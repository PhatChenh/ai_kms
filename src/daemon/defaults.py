"""
daemon/defaults.py

Baked compile-time constants for the daemon.
The DEFAULT_ENDPOINT is overridden at build time by the packager (Phase 7).
"""

from __future__ import annotations

DEFAULT_ENDPOINT: str = "https://your-cloud-endpoint.example.com"
