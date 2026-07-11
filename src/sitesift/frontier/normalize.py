"""URL normalization — ``url_norm`` is the primary key of the frontier.

The operation order below is exact and deterministic (spec §5.2). It is the one
function in the project that genuinely needs a large parametrized test suite, so
keep it pure, stdlib-based, and side-effect free.

Design choices worth stating (so tests are stable):

* Only ``http``/``https`` are targeted. A scheme-less input gets ``https://``.
  Non-http schemes are rejected by the pre-fetch filter, not here.
* Host is lowercased and IDN-encoded to punycode. IP literals (v4/v6) are left
  alone. Userinfo is preserved verbatim (the SSRF guard rejects userinfo hosts).
* Path case is **never** changed. Dot-segments (``.``/``..``) are resolved
  (RFC 3986 §5.2.4). Duplicate slashes are **kept** — they can be significant.
* Percent-encoding is canonicalized: hex digits uppercased, and unreserved
  characters that were needlessly encoded are decoded (RFC 3986 §6.2.2).
* Trailing slash is removed **only** when the path is empty or exactly ``/``.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import parse_qsl, quote, unquote_to_bytes, urlencode, urlsplit, urlunsplit

import idna

# Tracking params stripped by default (case-insensitive). Extendable via config.
DEFAULT_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "igshid",
        "ref",
        "ref_src",
        "ref_url",
        "source",
        "yclid",
        "wickedid",
    }
)

# RFC 3986 unreserved characters — safe to leave decoded.
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HEX = frozenset("0123456789abcdefABCDEF")

# Characters allowed to stay literal in a path component (not percent-encoded).
# Reserved sub-delims + others that are legal in paths per RFC 3986.
_PATH_SAFE = "/:@!$&'()*+,;=-._~"
_QUERY_SAFE = "-._~"


class NormalizationError(ValueError):
    """The input cannot be normalized into an http(s) URL."""


def normalize_url(
    raw: str,
    *,
    default_scheme: str = "https",
    tracking_params: frozenset[str] | None = None,
) -> str:
    """Return the canonical ``url_norm`` for ``raw``.

    Raises :class:`NormalizationError` for empty/uparseable input or a host that
    cannot be resolved to a name (an SSRF-relevant edge the filter also guards).
    """
    tracking = tracking_params if tracking_params is not None else DEFAULT_TRACKING_PARAMS

    s = _strip_controls(raw).strip()
    if not s:
        raise NormalizationError("empty URL")

    # Prepend a scheme if the author omitted it ("example.com/x" -> https://...).
    if "://" not in s.split("?", 1)[0].split("#", 1)[0]:
        # Guard against "mailto:foo" style (has ':' but no '//'): only prepend when
        # there is no scheme-like prefix at all.
        head = s.split("/", 1)[0]
        if ":" not in head:
            s = f"{default_scheme}://{s}"

    parts = urlsplit(s)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise NormalizationError(f"unsupported scheme: {parts.scheme!r}")

    host = _normalize_host(parts.hostname)
    if not host:
        raise NormalizationError("missing host")

    userinfo = _userinfo(parts)
    port = _normalize_port(scheme, parts.port)
    netloc = f"{userinfo}{host}{port}"

    path = _normalize_path(parts.path)
    query = _normalize_query(parts.query, tracking)
    fragment = ""  # always dropped

    return urlunsplit((scheme, netloc, path, query, fragment))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _strip_controls(s: str) -> str:
    # Drop Unicode control/format chars (category "C*": Cc control, Cf format
    # incl. bidi overrides, Cs/Co/Cn) that could hide the real target. Defense in
    # depth — the SSRF guard is the real gate.
    return "".join(ch for ch in s if not unicodedata.category(ch).startswith("C"))


def _normalize_host(hostname: str | None) -> str:
    if not hostname:
        return ""
    host = hostname.lower().rstrip(".")  # drop the (valid) trailing root dot
    if not host:
        return ""
    # IPv6 literal (urlsplit strips the brackets from .hostname) — leave as-is,
    # re-bracket in the netloc.
    if ":" in host:
        return f"[{host}]"
    # IPv4 literal — no IDN.
    if _looks_like_ipv4(host):
        return host
    # IDN -> punycode. Fall back to the lowercased host if idna refuses it (the
    # filter/guard will make the final call on whether it's fetchable).
    try:
        return idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        try:
            return host.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            return host


def _looks_like_ipv4(host: str) -> bool:
    labels = host.split(".")
    if len(labels) != 4:
        return False
    return all(label.isdigit() and 0 <= int(label) <= 255 for label in labels)


def _userinfo(parts: object) -> str:
    username = getattr(parts, "username", None)
    password = getattr(parts, "password", None)
    if username is None and password is None:
        return ""
    if password is not None:
        return f"{username or ''}:{password}@"
    return f"{username}@"


def _normalize_port(scheme: str, port: int | None) -> str:
    if port is None:
        return ""
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return ""
    return f":{port}"


def _normalize_path(path: str) -> str:
    if not path:
        return ""
    path = _canonicalize_percent(path, _PATH_SAFE)
    path = _remove_dot_segments(path)
    # Trailing slash removed only when the whole path is "/".
    if path == "/":
        return ""
    return path


def _normalize_query(query: str, tracking: frozenset[str]) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (k, v) for k, v in pairs if k.lower() not in tracking and not k.lower().startswith("utm_")
    ]
    if not kept:
        return ""
    kept.sort()  # (key, value) lexicographic — deterministic
    return urlencode(kept, quote_via=quote, safe=_QUERY_SAFE)


def _canonicalize_percent(s: str, safe: str) -> str:
    """Canonicalize percent-encoding in one pass.

    * ``%xx`` sequences: hex uppercased; if the byte decodes to an unreserved
      character it is decoded (needless encoding removed), otherwise the
      uppercased ``%XX`` is kept.
    * literal characters: kept if unreserved or in ``safe``; otherwise
      percent-encoded (e.g. spaces, control chars, non-ASCII).
    """
    safe_set = frozenset(safe)
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "%" and i + 2 < n and s[i + 1] in _HEX and s[i + 2] in _HEX:
            decoded = unquote_to_bytes(s[i : i + 3]).decode("latin-1")
            out.append(decoded if decoded in _UNRESERVED else "%" + s[i + 1 : i + 3].upper())
            i += 3
        elif ch in _UNRESERVED or ch in safe_set:
            out.append(ch)
            i += 1
        else:
            out.append(quote(ch, safe=""))
            i += 1
    return "".join(out)


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 §5.2.4 remove_dot_segments."""
    input_buf = path
    output: list[str] = []
    while input_buf:
        if input_buf.startswith("../"):
            input_buf = input_buf[3:]
        elif input_buf.startswith("./"):
            input_buf = input_buf[2:]
        elif input_buf.startswith("/./"):
            input_buf = "/" + input_buf[3:]
        elif input_buf == "/.":
            input_buf = "/"
        elif input_buf.startswith("/../"):
            input_buf = "/" + input_buf[4:]
            if output:
                output.pop()
        elif input_buf == "/..":
            input_buf = "/"
            if output:
                output.pop()
        elif input_buf in (".", ".."):
            input_buf = ""
        else:
            # Move the first path segment (including a leading '/') to output.
            slash_idx = input_buf.find("/", 1) if input_buf.startswith("/") else input_buf.find("/")
            if slash_idx == -1:
                output.append(input_buf)
                input_buf = ""
            else:
                output.append(input_buf[:slash_idx])
                input_buf = input_buf[slash_idx:]
    return "".join(output)
