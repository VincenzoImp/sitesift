"""Content-addressed blob store — a pure cache (deleting it costs only refetch).

Layout::

    <root>/blobs/ab/cd/abcd….zst    # zstd-compressed decompressed body
    <root>/meta/<url_sha1>.json      # {sha256, etag, last_modified, fetched_at, status}

The blob key is the sha256 of the *decompressed* response body, so identical
content fetched from different URLs is stored once.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import zstandard

_CCTX = zstandard.ZstdCompressor(level=10)
_DCTX = zstandard.ZstdDecompressor()


class BlobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def _blob_path(self, sha256: str) -> Path:
        return self.root / "blobs" / sha256[:2] / sha256[2:4] / f"{sha256}.zst"

    def _meta_path(self, url_norm: str) -> Path:
        url_sha1 = hashlib.sha1(url_norm.encode()).hexdigest()  # noqa: S324 - cache key, not security
        return self.root / "meta" / f"{url_sha1}.json"

    def put(self, content: bytes) -> str:
        """Store ``content``; return its sha256 (idempotent)."""
        sha = hashlib.sha256(content).hexdigest()
        path = self._blob_path(sha)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".zst.tmp")
            tmp.write_bytes(_CCTX.compress(content))
            tmp.replace(path)  # atomic
        return sha

    def get(self, sha256: str) -> bytes | None:
        path = self._blob_path(sha256)
        if not path.exists():
            return None
        return _DCTX.decompress(path.read_bytes())

    def has(self, sha256: str) -> bool:
        return self._blob_path(sha256).exists()

    def put_meta(self, url_norm: str, meta: dict[str, object]) -> None:
        path = self._meta_path(url_norm)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta), encoding="utf-8")

    def get_meta(self, url_norm: str) -> dict[str, object] | None:
        path = self._meta_path(url_norm)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        return data if isinstance(data, dict) else None
