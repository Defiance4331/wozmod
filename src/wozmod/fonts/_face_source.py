"""Load authored face sources from the art data directory."""

from pathlib import Path


_FACES = frozenset({"buttonvec", "headervec", "labelvec", "mainvec", "valuevec", "wo1vec", "menusans"})
_ROOT = Path(__file__).resolve().parents[3] / "data/art/faces"


def load(name: str, namespace: dict) -> None:
    if name not in _FACES:
        raise ValueError(f"unsupported authored face: {name}")
    source = _ROOT / f"{name}.py"
    exec(compile(source.read_bytes(), str(source), "exec"), namespace)
