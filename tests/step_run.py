"""Run one installer build step in this Python, as the installer's worker does (tests/test_step_keys.py).

    python tests/step_run.py STEP OUT JOBS     # JOBS 0: the installer's default worker count

Inputs: WOZMOD_GAME, WOZMOD_DONORS (the extracted discs) and WOZMOD_LETTERING. The step writes OUT/out, laid out as
the game folder. With WOZMOD_STEP_AUDIT=1 an audit hook, set once wozmod.install is loaded (the worker loads it to
run the step), writes OUT/audit.log: "open<TAB>path" for each file opened, "start<TAB>program" for each program
started, and "projected-elsewhere<TAB>file" when a projected file (step_keys.PROJECTED) is opened outside its
reader. Forked workers inherit the hook and append to the same log.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _audit(step: str, log: Path) -> None:
    from wozmod import resources
    from wozmod.install import step_keys

    root = resources.root()
    projected = {str(root / name): reader for name, (_, reader, _) in step_keys.PROJECTED.get(step, {}).items()}
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND, 0o644)

    def write(kind: str, value) -> None:
        os.write(descriptor, f"{kind}\t{os.fsdecode(value)}\n".encode("utf-8", "replace"))

    def hook(event: str, args) -> None:
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.abspath(os.fsdecode(args[0]))
            write("open", path)
            if path in projected:
                frame = sys._getframe(1)
                while frame is not None and frame.f_code.co_name != projected[path]:
                    frame = frame.f_back
                if frame is None:
                    write("projected-elsewhere", path)
        elif event == "subprocess.Popen":
            program = args[0]
            write("start", program if isinstance(program, (str, bytes, os.PathLike)) else program[0])
        elif event in ("os.posix_spawn", "os.exec", "os.spawn", "os.system"):
            write("start", args[0] if event != "os.spawn" else args[1])

    sys.addaudithook(hook)


def main(step: str, out: Path, jobs: int) -> None:
    from wozmod import install

    game, donors, lettering = (Path(os.environ[name]) for name in ("WOZMOD_GAME", "WOZMOD_DONORS", "WOZMOD_LETTERING"))
    jobs = jobs or install.worker_count()
    if os.environ.get("WOZMOD_STEP_AUDIT") == "1":
        _audit(step, out / "audit.log")
    target = out / "out"
    target.mkdir(parents=True, exist_ok=True)
    {"text": lambda: install._text_table(game, donors, target),
     "voices": lambda: install._voice_banks(game, donors, target, jobs=jobs),
     "art": lambda: install._menu_art(game, donors, lettering, out / "art-staging", target, jobs=jobs)}[step]()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    main(sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]))
