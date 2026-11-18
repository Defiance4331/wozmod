"""Run one installer build step in this Python and this environment, held to its manifest (tests/test_step_keys.py).

    python tests/step_run.py STEP OUT JOBS     # JOBS 0: the installer's default worker count

Inputs: WOZMOD_GAME, WOZMOD_DONORS (the extracted discs), WOZMOD_LETTERING and WOZMOD_SPEECH. The step writes OUT/out,
laid out as the game folder. Unlike an install's worker (install/steps.run), the environment is the caller's: the
test varies it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(step: str, out: Path, jobs: int) -> None:
    from wozmod import install
    from wozmod.install import gate, steps

    target = out / "out"
    target.mkdir(parents=True, exist_ok=True)
    steps.run_here(step, gate.ENFORCE, **steps.Inputs.environment(jobs or install.worker_count()).arguments(
        out / "staging", target))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    main(sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]))
