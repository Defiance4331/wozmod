"""Write a file that other processes may read at any moment: whole or absent, never partly written."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def replacing(path: str | Path) -> Iterator[Path]:
    """Yield a temporary sibling of path to write; on a clean exit it is flushed to disk and renamed over path.

    A reader opening path sees the old file or the finished new one. If the block raises, the temporary is removed
    and path keeps what it held (path itself is never opened, so a reader and a writer share nothing but the rename).
    The name carries the process id, so two writers in one folder do not share a temporary.
    """
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        yield temporary
        with open(temporary, "r+b") as written:  # fsync needs a writable handle on Windows
            os.fsync(written.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
