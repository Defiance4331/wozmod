"""The one way step code fans work out (tests/test_workers.py keeps it the only one): ordered_map.

A step's output must not depend on how many workers built it. ordered_map takes away the two ways it could: results
come back in the order the items were handed out, whatever order they finish in, and a worker is a thread of this
process that gets everything it reads as fn's argument, so it holds no state of its own (no module globals set for
it, no forked copy of them). Threads, on every system: the slow calls (the native codecs, hashlib, ffmpeg runs) let go
of the GIL, so threads run them side by side and share what the step has already loaded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def ordered_map(fn: Callable[[T], R], items: Iterable[T], jobs: int,
                done: Callable[[T], object] | None = None) -> Iterator[R]:
    """fn(item) for each item, at most jobs at once, yielded in items' order: each as soon as it and every item
    before it are done. Each item's work starts as soon as items gives it, so a generator makes its next item while
    the earlier ones run. An error is raised in items' order too: the first failing item's, once the items before it
    are done; the items after it that have not started never start.

    done(item), when given, runs here, one at a time, as each item finishes, in finishing order: for progress. It
    sees the item, never its result, so nothing but progress can follow the finishing order. When done raises (the
    player cancelled), the items not yet started never start and its error is raised.
    """
    if type(jobs) is not int or jobs < 1:
        raise ValueError(f"jobs must be a whole number of 1 or more, not {jobs!r}")
    with ThreadPoolExecutor(jobs) as pool:  # it starts a thread only for an item that finds none idle
        taken, futures = [], []
        try:
            for item in items:
                taken.append(item)
                futures.append(pool.submit(fn, item))
            at = {future: n for n, future in enumerate(futures)}
            pending, failed, given = set(futures), len(futures), 0
            while given < len(futures):
                if given == failed:
                    futures[failed].result()  # raises its error
                if futures[given] in pending:
                    finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in sorted(finished, key=at.__getitem__):
                        n = at[future]
                        if future.cancelled() or n > failed:
                            continue
                        if future.exception() is not None:
                            failed = n
                            for later in futures[n + 1:]:
                                later.cancel()
                        elif done is not None:
                            done(taken[n])
                    continue
                yield futures[given].result()
                given += 1
        finally:
            for future in futures:
                future.cancel()
