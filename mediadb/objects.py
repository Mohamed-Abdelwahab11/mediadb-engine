"""Content-addressed local object store and an explicit encryption-extension hook."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


class Codec(Protocol):
    def encode(self, plain: bytes) -> bytes: ...
    def decode(self, stored: bytes) -> bytes: ...


class IdentityCodec:
    """Development-only identity codec, NOT encryption."""

    def encode(self, plain: bytes) -> bytes:
        return plain

    def decode(self, stored: bytes) -> bytes:
        return stored


class ObjectStore:
    def __init__(self, directory: str | Path, codec: Codec | None = None):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.codec = codec or IdentityCodec()

    def put(self, payload: bytes) -> str:
        if not isinstance(payload, bytes):
            raise TypeError("object payload must be bytes")
        digest = hashlib.sha256(payload).hexdigest()
        dest = self.root / digest[:2] / digest
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            temporary = dest.with_suffix(".tmp")
            temporary.write_bytes(self.codec.encode(payload))
            temporary.replace(dest)
        return digest

    def get(self, digest: str) -> bytes:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid content-addressed object key")
        payload = self.codec.decode((self.root / digest[:2] / digest).read_bytes())
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("object integrity check failed")
        return payload
