"""LZP2 packing of the built entries, with its optional cache, and the worker pool the build stages share."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import lru_cache
from multiprocessing import get_all_start_methods, get_context
from pathlib import Path

from wozmod.formats.linkdata import encode_lzp2

LZP2_CACHE: Path | None = None

@lru_cache(maxsize=1)
def _lzp2_codec() -> bytes:
    """Cache key that changes with the codec. A packaged program has no source file, so it hashes the bytecode."""
    source = Path(encode_lzp2.__code__.co_filename)
    return hashlib.sha256(source.read_bytes() if source.is_file() else encode_lzp2.__code__.co_code).digest()

def worker_pool(workers: int):
    """Workers that share the already loaded donors: forked processes, or threads where fork does not exist (Windows)."""
    if "fork" in get_all_start_methods():
        return ProcessPoolExecutor(workers, mp_context=get_context("fork"))
    return ThreadPoolExecutor(workers)

def packed_lzp2(data: bytes) -> bytes:

    if LZP2_CACHE is None:
        return encode_lzp2(data)
    path = LZP2_CACHE / f"{hashlib.sha256(_lzp2_codec() + data).hexdigest()}.lzp2"
    if path.exists():
        return path.read_bytes()
    encoded = encode_lzp2(data)
    LZP2_CACHE.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(f".{os.getpid()}")
    partial.write_bytes(encoded)
    partial.replace(path)
    return encoded
