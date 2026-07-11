"""Pre-fetch filters — cheap rejections that never touch the network.

A URL that fails a filter is recorded as ``skipped_filter`` with a reason and is
never fetched.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Container
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048

# Path extensions that are never HTML pages worth classifying.
NON_HTML_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".bmp",
        ".tiff",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".json",
        ".xml",
        ".rss",
        ".atom",
        ".zip",
        ".gz",
        ".tar",
        ".rar",
        ".7z",
        ".bz2",
        ".exe",
        ".dmg",
        ".pkg",
        ".deb",
        ".rpm",
        ".msi",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".flac",
        ".wav",
        ".ogg",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
    }
)


def prefilter(
    url_norm: str,
    *,
    allow_ip_hosts: bool = False,
    allow_nonhtml: bool = False,
    exclude_hosts: Container[str] = frozenset(),
) -> str | None:
    """Return a skip reason (short string) if the URL should not be fetched, else None."""
    if len(url_norm) > MAX_URL_LENGTH:
        return "url_too_long"

    split = urlsplit(url_norm)
    if split.scheme not in ("http", "https"):
        return "bad_scheme"

    host = split.hostname or ""
    if not host:
        return "no_host"
    if host in exclude_hosts:
        return "excluded_host"

    if not allow_ip_hosts and _is_ip_literal(host):
        return "ip_literal_host"

    if not allow_nonhtml:
        path = split.path.lower()
        dot = path.rfind(".")
        if dot != -1 and path[dot:] in NON_HTML_EXTENSIONS:
            return "non_html_extension"

    return None


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False
