"""Bounded streams for files whose bytes occupy disc extents."""

from __future__ import annotations

import bisect
import io
import os
from dataclasses import dataclass
from pathlib import Path


class DiscError(ValueError):
    """A disc or archive does not satisfy its format."""


@dataclass(frozen=True)
class DiscFile:
    path: str
    size: int


class ExtentStream(io.RawIOBase):
    """A seekable, read-only view over ordered byte extents."""

    def __init__(self, path: Path, extents: tuple[tuple[int, int], ...]):
        super().__init__()
        self._source = Path(path).open("rb")
        source_size = os.fstat(self._source.fileno()).st_size
        self._extents = tuple(extents)
        self._ends: list[int] = []
        total = 0
        for offset, size in self._extents:
            if offset < 0 or size < 0 or offset + size > source_size:
                self._source.close()
                raise DiscError("File extent is outside the image")
            total += size
            self._ends.append(total)
        self._size = total
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._checkClosed()
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self._checkClosed()
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self._position + offset
        elif whence == os.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError("Invalid seek origin")
        if position < 0:
            raise ValueError("Negative seek position")
        self._position = position
        return position

    def readinto(self, buffer) -> int:
        self._checkClosed()
        view = memoryview(buffer).cast("B")
        remaining = min(len(view), max(0, self._size - self._position))
        written = 0
        while remaining:
            index = bisect.bisect_right(self._ends, self._position)
            start = self._ends[index - 1] if index else 0
            offset, size = self._extents[index]
            relative = self._position - start
            count = min(remaining, size - relative)
            self._source.seek(offset + relative)
            actual = self._source.readinto(view[written:written + count])
            if actual != count:
                raise DiscError("Truncated file extent")
            written += count
            remaining -= count
            self._position += count
        return written

    def close(self) -> None:
        if not self.closed:
            self._source.close()
        super().close()
