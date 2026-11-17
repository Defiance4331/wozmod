"""How a translation row is keyed: by the SHA-256 of its stock text, an event line with its voice id first. Kept apart
from text.build and text.stock so that a step that only looks rows up (the voices) does not load the DLL builder."""

from __future__ import annotations

import hashlib


def stock_key(raw: bytes) -> tuple[str, bytes] | None:
    """(SHA-256 hex, canonical CP932 bytes) that key a translation row; None when ``raw`` is not CP932."""
    try:
        canonical = raw.decode("cp932").encode("cp932")
    except UnicodeError:
        return None
    return hashlib.sha256(canonical).hexdigest(), canonical


def event_voice_form(text: bytes, voice: int) -> bytes:
    """An event, narration page or still event line with its voice id, as the runtime looks it up before the text alone
    (runtime event_line)."""
    return text + b"@V%04d" % voice
