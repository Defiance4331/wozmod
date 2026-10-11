"""Lettering request routing without font imports."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

ACTIVE = None
TRACE = os.environ.get("WOZMOD_ART_TRACE")
_UNTRACKED = 0


def key(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


class _Traced(Image.Image):
    """Marks its layer as drawn when the build reads its pixels outside a squish."""

    _marker: Path

    def load(self):
        if not _UNTRACKED:
            self._marker.touch()
        return super().load()


def _traced(image: Image.Image, folder: Path, identity: str) -> Image.Image:
    image.__class__ = _Traced
    image._marker = folder / f"{identity}.pixels"
    return image


def get(request: dict, producer) -> tuple[Image.Image, dict]:
    identity = key(request)
    if ACTIVE is not None:
        try:
            image, metrics = ACTIVE[identity]
        except KeyError:
            raise ValueError(f"lettering bundle is missing request {identity}") from None
        return image.copy(), dict(metrics, _lettering=identity)
    if TRACE:
        folder = Path(TRACE)
        folder.mkdir(parents=True, exist_ok=True)
        record = folder / f"{identity}.json"
        record.write_text(json.dumps(request, ensure_ascii=False, sort_keys=True))
        cached = folder / f"{identity}.png"
        metadata = folder / f"{identity}.metrics"
        if cached.exists() and metadata.exists():
            return (_traced(Image.open(cached).copy(), folder, identity),
                    dict(json.loads(metadata.read_text()), _lettering=identity))
    if request.get("kind") == "squish":
        with untracked():
            image, metrics = producer()
    else:
        image, metrics = producer()
    metrics = dict(metrics)
    if TRACE:
        image.save(cached)
        metadata.write_text(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
        image = _traced(image, folder, identity)
    return image, dict(metrics, _lettering=identity)


@contextmanager
def untracked():
    """Pixel reads here (squish sources, developer reports) do not ship a layer."""
    global _UNTRACKED
    _UNTRACKED += 1
    try:
        yield
    finally:
        _UNTRACKED -= 1


@contextmanager
def using(layers):
    global ACTIVE
    previous = ACTIVE
    ACTIVE = layers
    try:
        yield
    finally:
        ACTIVE = previous
