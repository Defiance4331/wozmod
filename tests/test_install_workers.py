"""How many workers the install starts by default."""

import pytest

from wozmod.install import GIB, default_workers


@pytest.mark.parametrize(("cpus", "total", "available", "workers"), [
    (16, 46 * GIB, 32 * GIB, 15),   # plenty of RAM: one core is left free
    (16, 16 * GIB, 12 * GIB, 15),   # 12 - 4 (a quarter) - 4.6 (the install) = 3.4 GiB: room for 27 more workers
    (16, 12 * GIB, 8 * GIB, 4),     # 8 - 3 - 4.6 = 0.4 GiB: three workers beside the first
    (16, 8 * GIB, 6 * GIB, 1),      # 6 - 2 (the 2 GiB floor) - 4.6 < 0: never below one worker
    (16, 7 * GIB, 27 * GIB // 4, 2),  # 6.75 - 2 (floor, not 1.75) - 4.6 = 0.15 GiB: two workers, not four
    (4, 32 * GIB, 30 * GIB, 3),     # the core limit still applies
    (1, 32 * GIB, 30 * GIB, 1),     # a single core still gets one worker
])
def test_default_workers_leave_a_core_and_a_quarter_of_ram_free(cpus, total, available, workers):
    # Bug: the headroom or the install's own need is left out of the budget (too many workers on a small PC), a
    # worker is charged more than the thread and ffmpeg it adds (one worker on a 12 GiB PC), the first worker is
    # charged twice, the 2 GiB floor is dropped, or the free core is not kept.
    assert default_workers(cpus, (total, available)) == workers


def test_unknown_memory_falls_back_to_the_core_limit():
    assert default_workers(8, None) == 7
