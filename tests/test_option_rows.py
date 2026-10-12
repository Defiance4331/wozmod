"""The Game Settings rows wozmod adds: their lettering sheet and the runtime that draws it."""
from __future__ import annotations

import ast
import re
import struct
from pathlib import Path

from PIL import Image

from wozmod.art import build
from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER, parse_g1t

C_SOURCE = Path(__file__).resolve().parents[1] / "runtime/english_runtime.c"


def c_define(name: str) -> int:
    return int(re.search(rf"#define {name} \(?(-?\d+)", C_SOURCE.read_text())[1])


def test_option_sheet_bands_follow_the_order_the_runtime_draws_them() -> None:
    """Bug: a text added to or dropped from OPTION_ROWS shifts every band after it, so the game shows
    FIELD OF VIEW under a FRAME RATE label or 105 where it means 100."""
    rows = build.OPTION_ROWS
    assert rows[:4] == ["TEXT LANGUAGE", "VOICE LANGUAGE", "FIELD OF VIEW", "FRAME RATE"]
    assert rows.index("ENGLISH") == c_define("BAND_ENGLISH") and rows[c_define("BAND_ENGLISH") + 1] == "JAPANESE"
    assert rows.index("60 FPS") == c_define("BAND_60") and rows[c_define("BAND_60") + 1] == "120 FPS"
    assert rows[c_define("BAND_FOV"):] == [f"< {v}% >" for v in range(
        c_define("FOV_MIN"), c_define("FOV_MAX") + 1, c_define("FOV_STEP"))]
    assert (c_define("OPTION_W"), c_define("OPTION_BAND"), c_define("OPTION_SCALE")) == (
        build.OPTION_W, build.OPTION_BAND, 2)


def test_every_option_text_is_in_the_label_face() -> None:
    """Bug: a character the label face lacks (the < > % of the field of view) fails the render or leaves the
    value blank; the face is read from its source, so no font tool is needed."""
    source = ast.parse((Path(__file__).resolve().parents[1] / "data/art/faces/buttonvec.py").read_text())
    params = next(node.value for node in source.body if isinstance(node, ast.Assign)
                  and getattr(node.targets[0], "id", "") == "PARAMS")
    have = {key.value for key in params.keys}
    assert sorted({ch for text in build.OPTION_ROWS for ch in text if ch != " "} - have) == []


def test_option_sheet_puts_each_text_in_its_own_band_centred_and_inside_its_room(monkeypatch) -> None:
    """Bug: a text drawn in the neighbouring band, off the quad centre, or wider than the room the stock labels
    keep; the values must also sit 3 px above the label baseline as the stock values do."""
    widths = {text: 120 + 14 * k for k, text in enumerate(build.OPTION_ROWS)}
    widths["VOICE LANGUAGE"] = 700  # wider than the 600 px room at 2x: must condense, never cut

    def fake(text, scale, out, track=0.0, style="501.3"):
        label = Image.new("RGBA", (widths[text] + 20, 80))
        label.paste((255, 255, 255, 255), (10, 20, 10 + widths[text], 60))
        return label, {"baseline": 60, "ink": [10, 20, 10 + widths[text], 60]}

    monkeypatch.setattr(build, "button_label", fake)
    art = build.option_rows_art(2, True)
    band = build.OPTION_BAND * 2
    assert art.size == (build.OPTION_W * 2, len(build.OPTION_ROWS) * band)
    wrong = []
    for k, text in enumerate(build.OPTION_ROWS):
        left, top, right, bottom = art.crop((0, k * band, art.width, (k + 1) * band)).getbbox()
        baseline = 68 if k < 4 else 62  # label baseline 34 px, values 31 px, at 2x
        if (abs((left + right) / 2 - build.OPTION_W) > 1 or right - left > build.OPTION_ROOM * 2
                or bottom != baseline):
            wrong.append((text, left, right, bottom))
    assert wrong == []


def test_hires_bank_carries_the_option_sheet_as_an_entry_zero_row(monkeypatch, tmp_path) -> None:
    """Bug: no entry-0 row, so the runtime finds no option sheet and silently draws the rows in the game font."""
    served = Image.new("RGBA", (64, 64))
    pixels = served.tobytes("raw", "BGRA")
    size = G1T_HEADER.size + 4 + G1T_TEXTURE_HEADER.size + len(pixels)
    entry = (G1T_HEADER.pack(b"GT1G", b"0000", size, G1T_HEADER.size, 1, 0, 0, 0)
             + struct.pack("<I", 4) + G1T_TEXTURE_HEADER.pack(0x10, 1, 0x66, 0) + pixels)
    entries = tmp_path / "wozmod/entries"
    entries.mkdir(parents=True)
    monkeypatch.setattr(build, "parse_texture_package", lambda data: type("P", (), {"items": [
        type("I", (), {"g1t": parse_g1t(entry)})() for _ in range(3)]})())
    monkeypatch.setattr(build, "HIRES", [(4069, 2, 2, 64, 64, 64, 0)])
    monkeypatch.setattr(build, "DARK", {})
    monkeypatch.setattr(build, "HIRES_NATIVE_MENU", set())
    monkeypatch.setattr(build, "vector_art", lambda *a, **k: Image.new("RGBA", (128, 128)))
    monkeypatch.setattr(build, "hires_texts", lambda *a: ["x"])
    monkeypatch.setattr(build, "option_rows_art",
                        lambda scale, squeeze: Image.new("RGBA", (build.OPTION_W * 2, 798 * 2)))
    (entries / "4069.bin").write_bytes(b"package")
    mapping = {"faces": {}, "textures": [{"entry": 4069, "item": 2, "rows": [[{"text": "x"}]]}]}
    blob = build.hires_bin(entries, None, (mapping, {}))
    rows = [struct.unpack_from("<9I", blob, 4 + 36 * k) for k in range(struct.unpack_from("<I", blob)[0])]
    assert [row[:7] for row in rows] == [(4069, 2, 2, 64, 64, 64, 0), (0, 0, 2, 310, 798, 42, 0)]
    sheet = parse_g1t(blob[rows[1][7]:rows[1][7] + rows[1][8]]).textures[0]
    assert (sheet.width, sheet.height) == (1024, 2048)
