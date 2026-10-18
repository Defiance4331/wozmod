"""The words the menu art letters, rebuilt at build time so the art data stores no official English.

Two stored forms stand in for a word, each with the SHA-256 of the word it must give:
- A letter map, on a harvested face row or the stage-name font: `"letters": {letter: "line:box line:box ..."}`
  says which letter sits at which box (non-space letters, in reading order) of each labelled ink line, and the line list
  (`texts`, `text` or `rows`) holds `{"boxes": n, "spaces": [box, ...], "sha256": ...}` per line, or null.
  `spaces` names the boxes a space follows.
- A pointer: `{"ref": [...], "edits": [[start, end, ""], ...], "case": "upper", "sha256": ...}` names a donor
  string (resolved by the text table's DonorResolver), and `{"label": [face, row, line], ...}` a face label.
  Edits are ascending `[start, end, replacement]` spans in the resolved string, replacements our own text (as in
  the text table); `case` applies after them.
"""
from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path

from wozmod import resources
from wozmod.text.resolver import DonorResolver

MAPPING = resources.root() / "data/art/menu_textures.json"
CASES = {"upper": str.upper, "lower": str.lower, "title": str.title}
LINES = ("texts", "text", "rows")  # the keys a letter map's lines sit under
DONORS: DonorResolver | None = None


def use(root: Path | None) -> None:
    """Resolve donor pointers from this donors folder (laid out as the installer's, as for the text table)."""
    global DONORS
    DONORS = DonorResolver(Path(root)) if root else None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path):
    """An art data file with every stored form replaced by its word (a fresh copy: callers may change it)."""
    data = lettered(json.loads(Path(path).read_text(encoding="utf-8")))
    return _pointed(data, (data if "faces" in data else _labels())["faces"])


def resolved(value):
    """A piece of art data (for example one catalog row) with its stored forms replaced by their words."""
    return _pointed(lettered(value), _labels()["faces"])


@cache
def _labels() -> dict:
    return lettered(json.loads(MAPPING.read_text(encoding="utf-8")))


def _checked(text: str, expected: str, what: str) -> str:
    if sha256(text) != expected:
        raise ValueError(f"{what} does not give the word it names (SHA-256 {expected[:12]}…)")
    return text


def _line(spec: dict, boxes: list, k: int) -> str:
    if None in boxes:
        raise ValueError(f"letter map line {k} leaves box {boxes.index(None)} empty")
    for box in sorted(spec.get("spaces", []), reverse=True):
        boxes.insert(box + 1, " ")
    return _checked("".join(boxes), spec["sha256"], f"letter map line {k}")


def lettered(value):
    """`value` with each letter map rebuilt into its labels (no donors needed)."""
    if isinstance(value, list):
        return [lettered(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "letters" not in value:
        return {key: lettered(item) for key, item in value.items()}
    key = next(key for key in LINES if key in value)
    specs = value[key] if isinstance(value[key], list) else [value[key]]
    boxes = [None if spec is None else [None] * spec["boxes"] for spec in specs]
    for letter, places in value["letters"].items():
        for line, box in (map(int, place.split(":")) for place in places.split()):
            if boxes[line][box] is not None:
                raise ValueError(f"letter map puts two letters in line {line} box {box}")
            boxes[line][box] = letter
    texts = [None if spec is None else _line(spec, line, k) for k, (spec, line) in enumerate(zip(specs, boxes, strict=True))]
    out = {name: lettered(item) for name, item in value.items() if name != "letters"}
    out[key] = texts if isinstance(value[key], list) else texts[0]
    return out


def _pointed(value, faces: dict):
    if isinstance(value, list):
        return [_pointed(item, faces) for item in value]
    if not isinstance(value, dict):
        return value
    if "sha256" not in value or not {"ref", "label"} & value.keys():
        return {key: _pointed(item, faces) for key, item in value.items()}
    if "label" in value:
        face, row, line = value["label"]
        source = faces[face]["rows"][row]
        text = source["texts"][line] if "texts" in source else source["text"]
    elif DONORS is None:
        raise ValueError("the art words need the donors: call wozmod.art.words.use(<donors folder>) first")
    else:
        text = DONORS.resolve(value["ref"])
    end, chunks = 0, []
    for start, stop, replacement in value.get("edits", []):
        if not end <= start <= stop <= len(text):
            raise ValueError("art word edits must be ascending spans inside the donor string")
        chunks.extend((text[end:start], replacement))
        end = stop
    text = "".join((*chunks, text[end:]))
    if "case" in value:
        text = CASES[value["case"]](text)
    return _checked(text, value["sha256"], f"art word {value.get('ref') or value['label']}")
