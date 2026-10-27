"""The Game Settings rows wozmod adds: their lettering sheet and the runtime that draws it."""
from __future__ import annotations

import ast
import struct
from pathlib import Path

from PIL import Image

from wozmod.art import vector
from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER, parse_g1t
from wozmod.runtime.layout import DEFINES


def test_option_sheet_bands_follow_the_order_the_runtime_draws_them() -> None:
    """Bug: a text added to or dropped from OPTION_ROWS shifts every band after it, so the game shows
    FIELD OF VIEW under a FRAME RATE label or WINDOWED where it means FULLSCREEN."""
    rows = vector.OPTION_ROWS
    assert rows[:vector.OPTION_LABELS] == ["TEXT LANGUAGE", "VOICE LANGUAGE", "FIELD OF VIEW", "FRAME RATE",
                                           "MOVING CAMERA FIX", "VSYNC"]
    assert rows.index("VSYNC") == DEFINES["BAND_VSYNC"]
    assert rows.index("ENGLISH") == DEFINES["BAND_ENGLISH"] and rows[DEFINES["BAND_ENGLISH"] + 1] == "JAPANESE"
    assert rows[DEFINES["BAND_DISPLAY"]:DEFINES["BAND_DISPLAY"] + 3] == ["FULLSCREEN", "WINDOWED", "BORDERLESS"]
    assert len(rows) == DEFINES["BAND_DISPLAY"] + 3  # the field of view and frame rate values are plain text
    assert (DEFINES["OPTION_W"], DEFINES["OPTION_BAND"]) == (vector.OPTION_W, vector.OPTION_BAND)


def test_every_option_text_is_in_the_label_face() -> None:
    """Bug: a character the label face lacks (the digits of the frame rates) fails the render or leaves the
    value blank; the face is read from its source, so no font tool is needed."""
    source = ast.parse((Path(__file__).resolve().parents[1] / "data/art/faces/buttonvec.py").read_text())
    params = next(node.value for node in source.body if isinstance(node, ast.Assign)
                  and getattr(node.targets[0], "id", "") == "PARAMS")
    have = {key.value for key in params.keys}
    assert sorted({ch for text in vector.OPTION_ROWS for ch in text if ch != " "} - have) == []


def test_option_sheet_puts_each_text_in_its_own_band_centred_and_inside_its_room(letters) -> None:
    """Bug: a text drawn in the neighbouring band, off the quad centre, or wider than the room the stock labels
    keep; the values must also sit 3 px above the label baseline as the stock values do."""
    widths = {text: 120 + 14 * k for k, text in enumerate(vector.OPTION_ROWS)}
    widths["VOICE LANGUAGE"] = 700  # wider than the 600 px room at 2x: must condense, never cut

    def fake(request):
        text = request["text"]
        label = Image.new("RGBA", (widths[text] + 20, 80))
        label.paste((255, 255, 255, 255), (10, 20, 10 + widths[text], 60))
        return label, {"baseline": 60, "ink": [10, 20, 10 + widths[text], 60]}

    letters(fake)
    art = vector.option_rows_art(2, True)
    band = vector.OPTION_BAND * 2
    assert art.size == (vector.OPTION_W * 2, len(vector.OPTION_ROWS) * band)
    wrong = []
    for k, text in enumerate(vector.OPTION_ROWS):
        left, top, right, bottom = art.crop((0, k * band, art.width, (k + 1) * band)).getbbox()
        baseline = 68 if k < vector.OPTION_LABELS else 62  # label baseline 34 px, values 31 px, at 2x
        if (abs((left + right) / 2 - vector.OPTION_W) > 1 or right - left > vector.OPTION_ROOM * 2
                or bottom != baseline):
            wrong.append((text, left, right, bottom))
    assert wrong == []


def test_hires_bank_carries_the_option_sheet_as_an_entry_zero_row(monkeypatch, tmp_path) -> None:
    """Bug: no entry-0 row, so the runtime finds no option sheet and silently draws the rows in the game font."""
    from wozmod.art.hires import read_hires_bank

    served = Image.new("RGBA", (64, 64))
    rows_h = len(vector.OPTION_ROWS) * vector.OPTION_BAND
    pixels = served.tobytes("raw", "BGRA")
    size = G1T_HEADER.size + 4 + G1T_TEXTURE_HEADER.size + len(pixels)
    entry = (G1T_HEADER.pack(b"GT1G", b"0000", size, G1T_HEADER.size, 1, 0, 0, 0)
             + struct.pack("<I", 4) + G1T_TEXTURE_HEADER.pack(0x10, 1, 0x66, 0) + pixels)
    entries = tmp_path / "wozmod/entries"
    entries.mkdir(parents=True)
    monkeypatch.setattr(vector, "parse_texture_package", lambda data: type("P", (), {"items": [
        type("I", (), {"g1t": parse_g1t(entry)})() for _ in range(3)]})())
    monkeypatch.setattr(vector, "HIRES", [(4069, 2, 64, 64, 64, 0)])
    monkeypatch.setattr(vector, "DARK", {})
    monkeypatch.setattr(vector, "HIRES_NATIVE_MENU", set())
    monkeypatch.setattr(vector, "vector_art", lambda *a, **k: Image.new("RGBA", (64 * vector.HIRES_SCALE,) * 2))
    monkeypatch.setattr(vector, "hires_texts", lambda *a: ["x"])
    monkeypatch.setattr(vector, "option_rows_art",
                        lambda scale, squeeze: Image.new("RGBA", (vector.OPTION_W * scale, rows_h * scale)))
    (entries / "4069.bin").write_bytes(b"package")
    mapping = {"faces": {}, "textures": [{"entry": 4069, "item": 2, "rows": [[{"text": "x"}]]}]}
    blob = vector.hires_bin(entries, (mapping, {}))
    rows = read_hires_bank(blob)
    scale = vector.HIRES_SCALE
    assert [(row.entry, row.item, row.w, row.h, row.band, row.per_col) for row in rows] == [
        (4069, 2, 64, 64, 64, 0), (0, 0, 310, rows_h, 42, 0)]
    assert len(rows[1].blocks[0][0]) == -(-310 * scale // 8) * 8 * -(-rows_h * scale // 8) * 8  # the whole sheet


def test_display_mode_plates_step_by_their_lettering_room() -> None:
    """Bug: the runtime's display-mode pitch changes but the lettering room still follows the old one, so the words
    are squeezed for nothing or overrun the margin between them."""
    room = DEFINES["DISPLAY_MODE_PITCH"] - vector.VALUE_MARGIN
    assert {vector.OPTION_NARROW[t] for t in ("FULLSCREEN", "WINDOWED", "BORDERLESS")} == {room}


def test_option_value_sheets_letter_each_word_in_the_label_face_in_its_look(letters) -> None:
    """Bug: a stock value sheet keeps the serif value face, comes out white or in the other look's colour, or sits off
    the baseline the runtime's own values keep, so a row's values differ from the row beside it."""
    def fake(request):
        if request["kind"] != "button":
            raise AssertionError("an option value was lettered in the serif value face")
        label = Image.new("RGBA", (120, 80))
        label.paste((255, 255, 255, 255), (10, 20, 110, 60))
        return label, {"baseline": 60, "ink": [10, 20, 110, 60], "fill": [10, 110]}

    letters(fake)
    served = Image.new("RGBA", (140, 84))
    for k in range(2):
        served.paste((253, 238, 156, 255), (30, 42 * k + 10, 110, 42 * k + 31))
    mapping = {"faces": {}, "textures": [{"entry": 4069, "item": 15, "slots": [[0, 140], [0, 140]], "draw": [
        {"look": "value_lit", "text": text, "into": [0, 8 + 42 * k, 140, 40 + 42 * k]}
        for k, text in enumerate(("ALDER", "BIRCH"))]}]}
    edit = vector.rows_edit(4069, 15, mapping, served, look="value_lit")
    art = vector.vector_art(edit, served, vector.hires_texts(edit, mapping, {}), 2, None, True)
    for k in range(2):
        band = art.crop((0, 84 * k, art.width, 84 * (k + 1)))
        left, _, right, bottom = band.getbbox()
        # runtime value_lit 0x7F, 0x77, 0x4E (0x80 = 1) on white; values on the 31 px baseline, centred in the slot
        assert ({band.getpixel((x, 40))[:3] for x in range(left, right)}, bottom, (left + right) / 2) == (
            {(253, 237, 155)}, 62, 140)
