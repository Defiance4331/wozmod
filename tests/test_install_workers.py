"""How many workers the install starts by default."""

import pytest

from wozmod.install import GIB, default_workers


@pytest.mark.parametrize(("cpus", "total", "available", "workers"), [
    (16, 46 * GIB, 32 * GIB, 15),   # plenty of RAM: one core is left free
    (8, 8 * GIB, 6 * GIB, 7),       # 6 - 2 (the 2 GiB floor) - 2 (the install) = 2 GiB: an 8 GiB PC keeps 7 workers
    (16, 8 * GIB, 5 * GIB, 9),      # 5 - 2 - 2 = 1 GiB: eight workers beside the first
    (16, 8 * GIB, 7 * GIB // 2, 1),  # 3.5 - 2 - 2 < 0: never below one worker
    (16, 7 * GIB, 17 * GIB // 4, 3),  # 4.25 - 2 (floor, not 1.75) - 2 = 0.25 GiB: three workers, not five
    (16, 16 * GIB, 13 * GIB // 2, 5),  # 6.5 - 4 (a quarter) - 2 = 0.5 GiB: five workers, not 15
    (4, 32 * GIB, 30 * GIB, 3),     # the core limit still applies
    (1, 32 * GIB, 30 * GIB, 1),     # a single core still gets one worker
])
def test_default_workers_leave_a_core_and_a_quarter_of_ram_free(cpus, total, available, workers):
    # Bug: the headroom or the install's own need is left out of the budget (too many workers on a small PC), a
    # worker is charged more than the thread and ffmpeg it adds (three workers, not nine, with 5 GiB free), the first
    # worker is charged twice, the 2 GiB floor or the quarter is dropped, or the free core is not kept.
    assert default_workers(cpus, (total, available)) == workers


def test_unknown_memory_falls_back_to_the_core_limit():
    assert default_workers(8, None) == 7
