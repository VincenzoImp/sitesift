"""Stable error-code taxonomy.

Every failure that gets persisted to the frontier (``urls.last_error_code``) or
logged for post-run triage uses one of these codes. The string values are a
**stable API**: downstream tooling filters on them, so never rename an existing
code — add a new one.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes (see docs/architecture.md §errors)."""

    # --- Network / fetch ---------------------------------------------------
    E_DNS = "E_DNS"  # name does not resolve
    E_CONNECT = "E_CONNECT"  # connection refused / reset
    E_TLS = "E_TLS"  # TLS handshake / certificate failure
    E_TIMEOUT = "E_TIMEOUT"  # connect / read / total timeout
    E_TOO_LARGE = "E_TOO_LARGE"  # body exceeds max_body_bytes
    E_BOMB = "E_BOMB"  # decompression bomb / ratio exceeded
    E_NONHTML = "E_NONHTML"  # content-type not (x)html
    E_REDIRECT_LOOP = "E_REDIRECT_LOOP"  # too many / cyclic redirects

    # --- Politeness / access ----------------------------------------------
    E_ROBOTS_BLOCK = "E_ROBOTS_BLOCK"  # disallowed by robots.txt
    E_ROBOTS_UNAVAIL = "E_ROBOTS_UNAVAIL"  # robots.txt 5xx/timeout -> disallow-all
    E_SSRF_BLOCKED = "E_SSRF_BLOCKED"  # resolved to a forbidden IP / port
    E_HTTP_4XX = "E_HTTP_4XX"
    E_HTTP_5XX = "E_HTTP_5XX"
    E_RATE_LIMIT = "E_RATE_LIMIT"  # 429/503 with Retry-After

    # --- Extraction --------------------------------------------------------
    E_PARSE = "E_PARSE"  # HTML could not be parsed
    E_EXTRACT_EMPTY = "E_EXTRACT_EMPTY"  # no usable text extracted

    # --- Classification / LLM ---------------------------------------------
    E_LLM_INVALID = "E_LLM_INVALID"  # structured output failed validation twice
    E_LLM_RATE = "E_LLM_RATE"  # provider 429 / overloaded
    E_LLM_AUTH = "E_LLM_AUTH"  # provider auth failure
    E_BUDGET = "E_BUDGET"  # local cost budget exceeded


class SiteSiftError(Exception):
    """Base exception carrying a stable :class:`ErrorCode`.

    Raise this (or a subclass) instead of bare exceptions in the pipeline so the
    frontier can persist ``code`` for later triage.
    """

    def __init__(self, code: ErrorCode, message: str = "", *, retryable: bool | None = None):
        self.code = code
        self.message = message or code.value
        # Sensible default: network + provider-throttling + robots-unavailable are retryable.
        if retryable is None:
            retryable = code in _RETRYABLE
        self.retryable = retryable
        super().__init__(f"{code.value}: {self.message}" if message else code.value)


class FetchError(SiteSiftError):
    """A failure during phase 2 (fetch)."""


class SSRFBlocked(FetchError):
    """The resolved address/port is not allowed."""

    def __init__(self, message: str):
        super().__init__(ErrorCode.E_SSRF_BLOCKED, message, retryable=False)


class ExtractError(SiteSiftError):
    """A failure during phase 3 (extract)."""


class ClassifyError(SiteSiftError):
    """A failure during phase 4 (classify)."""


class ConfigError(Exception):
    """Invalid or missing configuration.

    Process-level, not a per-URL failure, so it carries no :class:`ErrorCode`.
    The CLI maps it to exit code 2.
    """


_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.E_CONNECT,
        ErrorCode.E_TIMEOUT,
        ErrorCode.E_HTTP_5XX,
        ErrorCode.E_RATE_LIMIT,
        ErrorCode.E_ROBOTS_UNAVAIL,
        ErrorCode.E_LLM_RATE,
    }
)
