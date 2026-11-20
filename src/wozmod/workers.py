"""The one way step code fans work out (tests/test_workers.py keeps it the only one): ordered_map.

A step's output must not depend on how many workers built it. ordered_map takes away the two ways it could: results
come back in the order the items were handed out, whatever order they finish in, and a worker is a thread of this
process that gets everything it reads as fn's argument, so it holds no state of its own (no module globals set for
it, no forked copy of them). Threads, on every system: the slow calls (the native codecs, hashlib, ffmpeg runs) let go
of the GIL, so threads run them side by side and share what the step has already loaded.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")
_END = object()  # what next() gives once items has no more


def ordered_map(fn: Callable[[T], R], items: Iterable[T], jobs: int,
                done: Callable[[T], object] | None = None) -> Iterator[R]:
    """fn(item) for each item, at most jobs at once, yielded in items' order: each as soon as it and every item
    before it are done. items is read as the workers come free, one item ahead of them: a generator makes its next
    item while the earlier ones run, and at most jobs + 1 items are taken and not yet done. Each result is let go of
    once it is yielded. An error is raised in items' order too: the first failing item's, once the items before it
    are done; the items after it that have not started never start.

    done(item), when given, runs here, one at a time, as each item finishes, in finishing order: for progress. It
    sees the item, never its result, so nothing but progress can follow the finishing order. When done raises (the
    player cancelled), the items not yet started never start and its error is raised.
    """
    if type(jobs) is not int or jobs < 1:
        raise ValueError(f"jobs must be a whole number of 1 or more, not {jobs!r}")
    taking = iter(items)
    with ThreadPoolExecutor(jobs) as pool:  # it starts a thread only for an item that finds none idle
        given: deque[Future] = deque()  # taken and not yet yielded, in items' order
        running: dict[Future, tuple[int, T]] = {}  # taken and not yet seen to finish: its place in items, the item
        taken, failed = 0, None  # failed: the place of the first item in items' order seen to fail
        try:
            while True:
                if failed is None and len(running) <= jobs and (item := next(taking, _END)) is not _END:
                    future = pool.submit(fn, item)
                    given.append(future)
                    running[future] = taken, item
                    taken += 1
                elif not given:
                    return
                elif given[0] not in running:
                    yield given.popleft().result()  # raises its error
                else:
                    finished, _ = wait(running, return_when=FIRST_COMPLETED)
                    for future in sorted(finished, key=lambda f: running[f][0]):
                        n, item = running.pop(future)
                        if future.cancelled() or failed is not None and n > failed:
                            continue  # past the first failure: its error is what is raised, and no progress after
                        if future.exception() is not None:
                            failed = n
                            for later in list(given)[given.index(future) + 1:]:
                                later.cancel()
                        elif done is not None:
                            done(item)
        finally:
            for future in given:
                future.cancel()
