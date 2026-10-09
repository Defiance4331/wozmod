from __future__ import annotations

import struct

import pytest
from PIL import Image

from wozmod.art import build

FILL, OUTLINE = (220, 180, 40, 255), (8, 4, 0, 255)


class Texture:
    def __init__(self, image):
        self.width, self.height, self.image = *image.size, image

    def decode_rgba(self):
        return self.image.tobytes()


class Target(Texture):
    pixel_format, payload = 8, b"image"

    def encode_rgba(self, data):
        return Image.frombytes("RGBA", (self.width, self.height), data)



def test_spacing_fit_does_not_drop_space_contribution():
    right, left, space = {"a": 10, "b": 7, "c": 12}, {"a": 1, "b": 3, "c": 2}, 6
    pairs = [(x, y, n, right[x] + left[y] + space * n) for x in "abc" for y in "abc" for n in (0, 1)]
    got_right, got_left, got_space = build.fit_spacing(pairs)
    assert abs(got_space - space) < 1e-6
    for x in "abc":
        for y in "abc":
            assert abs(got_right[x] + got_left[y] - right[x] - left[y]) < 1e-6


def test_glyph_fills_keeps_a_dot_and_stem_as_one_letter():
    row = Image.new("RGBA", (40, 12), OUTLINE)
    for box in ((2, 2, 6, 10), (12, 1, 14, 3), (12, 5, 14, 10), (20, 2, 24, 10)):
        row.paste(FILL, box)
    fills = build.glyph_fills(row, "a ib")
    assert [len(f) for f in fills] == [32, 4 + 10, 32]


def test_dream_donor_crop_is_not_shifted_or_resampled():
    import struct
    from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER

    def texture(image):
        dimensions = ((image.width.bit_length() - 1)
                      | (image.height.bit_length() - 1) << 4)
        pixels = image.tobytes("raw", "BGRA")
        size = G1T_HEADER.size + 4 + G1T_TEXTURE_HEADER.size + len(pixels)
        return (G1T_HEADER.pack(b"GT1G", b"0000", size, G1T_HEADER.size, 1, 0, 0, 0)
                + struct.pack("<I", 4) + G1T_TEXTURE_HEADER.pack(0x10, 1, dimensions, 0)
                + pixels)

    small = texture(Image.new("RGBA", (4, 4), (1, 2, 3, 255)))
    square = texture(Image.new("RGBA", (256, 256), (90, 60, 20, 255)))
    package = (b"".join(small + bytes(2048 - len(small)) for _ in range(64))
               + square + bytes(-len(square) % 2048))
    source = Image.frombytes("RGBA", (256, 512), bytes(
        channel for y in range(512) for x in range(256)
        for channel in (x, y % 256, (3 * x + 7 * y) % 256, 255)))
    donors = {("wo2", 27): {105: build.parse_g1t(texture(source))}}
    edit = {"entry": 4095, "item": 64, "texture": 0,
            "card": {"wo2": [27, 105, 0], "box": [0, 40, 196, 236]}}
    result = build.parse_texture_package(build.edited_entry(package, [edit], donors))
    served = result.items[64].g1t.textures[0]
    expected = Image.new("RGBA", (256, 256))
    expected.paste(source.crop((0, 40, 196, 236)), (0, 0))
    assert (served.width, served.height, served.decode_rgba()) == (
        256, 256, expected.tobytes())
    assert result.items[63].source == small


def test_stage_title_redraws_only_a_condensed_donor():
    line = Image.new("RGBA", (30, 20))
    line.paste(FILL, (0, 4, 30, 16))
    face = type("Face", (), {"render": lambda self, text: (line, 16)})()

    def donors(wo1_width):
        art = Image.new("RGBA", (64, 32))
        art.paste(FILL, (0, 10, wo1_width, 22))
        texture = Target(art)
        texture.payload = b"wo1!!"
        return {"faces": {"title": face}, 5: {0: type("G1t", (), {"textures": [texture]})}}

    edit, target = {"stage_title": "Title", "baseline": 22, "wo1": [5, 0, 0]}, Target(Image.new("RGBA", (64, 32)))
    assert build.payload(target, edit, donors(30), None) == b"wo1!!"
    drawn = build.payload(target, edit, donors(24), None)
    assert drawn.getbbox() == (0, 10, 30, 22)


def test_shadow_does_not_paint_under_antialiased_border_or_opening():
    white, dark = (240, 240, 240, 255), (49, 52, 49, 255)
    art = Image.new("RGBA", (4, 5), white)
    art.paste(dark, (0, 0, 3, 2))
    art.putpixel((2, 1), (106, 107, 106, 255))
    edit = {"shadow": {"from": 2, "rgb": [[164, 168, 159], [222, 227, 214]]}}
    image = build.payload(Target(art), edit, {}, None)
    assert [image.getpixel((x, 2))[0] for x in range(4)] == [164, 164, 240, 240]
    assert [image.getpixel((x, 3))[0] for x in range(4)] == [222, 222, 240, 240]
    assert image.getpixel((0, 4)) == white


def test_composed_row_does_not_drop_prefix_strokes():
    stock = Image.new("RGBA", (64, 32))
    for x in (0, 8, 50):
        stock.paste(FILL, (x, 10, x + 4, 18))
    word = Image.new("RGBA", (16, 32))
    word.paste(FILL, (0, 10, 10, 18))
    donors = {1: {2: type("G1t", (), {"textures": [Texture(word)]})}}
    rows = [[{"box": (0, 0, 64, 32), "strokes": 2}, {"donor": [1, 2, 0], "box": (0, 0, 16, 32)}]]
    alpha = build.composed(stock, rows, donors, "centre", 3).getchannel("A")
    assert alpha.getbbox() == (14, 10, 39, 18)
    assert [alpha.getpixel((x, 12)) for x in (14, 18, 22, 26, 29, 38)] == [255, 0, 255, 0, 255, 255]


def test_hires_sheet_does_not_swap_band_rows_and_columns():
    art = Image.new("RGBA", (8, 128))
    for b in range(8):
        art.paste((b + 1, 0, 0, 255), (0, b * 16, 8, b * 16 + 16))
    sheet = build.hires_sheet(art, 4, 8, 2, 2)
    assert sheet.size == (32, 32)
    assert [sheet.getpixel(((b // 2) * 8, (b % 2) * 16))[0] for b in range(8)] == list(range(1, 9))
    assert sheet.getpixel((16, 31)) == (6, 0, 0, 255)


def test_native_plate_does_not_requantize_surrounding_pixels():
    import struct
    from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER

    blocks = [struct.pack("<BB6sHHI", 204, 201, bytes(6), 0xDEF8 + i, 0xAD55 + i, 0x55555555)
              for i in range(4)]
    size = G1T_HEADER.size + 4 + G1T_TEXTURE_HEADER.size + 64
    g1t = build.parse_g1t(G1T_HEADER.pack(b"GT1G", b"0000", size, G1T_HEADER.size, 1, 0, 0, 0)
                          + struct.pack("<I", 4) + G1T_TEXTURE_HEADER.pack(0x10, 8, 0x33, 0)
                          + b"".join(blocks))
    original = build.rgba(g1t.textures[0])
    image = original.copy()
    image.paste((220, 221, 220, 204), (1, 1, 3, 3))
    native = build.native_plate(g1t, image)
    assert native.textures[0].pixel_format == 1
    assert build.rgba(native.textures[0]).tobytes() == image.tobytes()


def test_vector_overflow_fails_instead_of_leaving_native_pixels(monkeypatch, tmp_path):
    import pytest

    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    served = Image.new("RGBA", (16, 32))
    served.paste((41, 40, 41, 255), (2, 8, 14, 25))
    label = Image.new("RGBA", (60, 48), (41, 40, 41, 255))
    monkeypatch.setattr(build, "header_label", lambda *a: (label, {"ink": [1, 1, 59, 47], "baseline": 40}))
    edit = {"entry": 4080, "item": 7, "style": "pause", "rows": [[{"text": "Alder archive"}]], "bands": [(0, 32)]}
    with pytest.raises(ValueError, match="full-width ink.*leaves the 16-wide texture"):
        build.vector_art(edit, served, ["Alder archive"], 2)


def test_vector_dark_row_does_not_take_a_long_crossbar_as_baseline(monkeypatch, tmp_path):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    served = Image.new("RGBA", (64, 32))
    served.paste((41, 40, 41, 255), (7, 9, 26, 23))
    served.paste((41, 40, 41, 255), (26, 9, 62, 12))
    label = Image.new("RGBA", (48, 42))
    label.paste((41, 40, 41, 255), (4, 8, 44, 36))
    monkeypatch.setattr(build, "header_label", lambda *a: (label, {"ink": [4, 8, 44, 36], "baseline": 36}))
    edit = {"entry": 4080, "item": 6, "style": "pause", "rows": [[{"text": "Menu"}]], "bands": [(0, 32)]}
    assert build.vector_art(edit, served, ["Menu"], 2).getbbox() == (14, 18, 54, 46)


def test_squeeze_does_not_shrink_fitting_rows_or_default_build(monkeypatch, tmp_path):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    label = Image.new("RGBA", (48, 42))
    label.paste((41, 40, 41, 255), (4, 8, 44, 36))
    monkeypatch.setattr(build, "header_label", lambda *a: (label, {"ink": [4, 8, 44, 36], "baseline": 36}))
    edit = {"entry": 4080, "item": 6, "style": "pause", "rows": [[{"text": "Menu"}]], "bands": [(0, 32)]}
    for official_width in (24, 12, 8):
        left = 0 if official_width == 8 else 7
        served = Image.new("RGBA", (64, 32))
        served.paste((41, 40, 41, 255), (left, 9, left + official_width, 23))
        default = build.vector_art(edit, served, ["Menu"], 2)
        assert default.tobytes() == build.vector_art(edit, served, ["Menu"], 2, squeeze=False).tobytes()
        squeezed = build.vector_art(edit, served, ["Menu"], 2, squeeze=True)
        if official_width == 24:
            assert squeezed.tobytes() == default.tobytes()
        else:
            fill = squeezed.getchannel("A").point(lambda a: 255 if a > 128 else 0)
            assert fill.getbbox() == (left * 2, 18, (left + official_width) * 2, 46)


def test_serif_renderer_does_not_alias_distinct_donor_references(monkeypatch, tmp_path):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    references = {"493.13": (220, 180, 40, 255), "508.34": (221, 180, 40, 255),
                  "506.9": (201, 100, 40, 255), "506.10": (251, 100, 40, 255)}

    def render(text, scale, out, raster=build.HEADER_RASTER, *options):
        ref = options[options.index("--ref") + 1] if "--ref" in options else ""
        label = Image.new("RGBA", (40, 40), references.get(ref, (255, 0, 255, 255)))
        return label, {"ink": [0, 0, 40, 40], "baseline": 32}

    monkeypatch.setattr(build, "header_label", render)
    for entry, item, ref, face in [(4060, 33, "493.13", {"face": "header"}),
                                   (4080, 38, "508.34", {"face": "header"}),
                                   (4077, 20, "506.9", {"look": "choice_dim"}),
                                   (4077, 22, "506.10", {"look": "choice_lit"})]:
        served = Image.new("RGBA", (128, 56))
        served.paste(references[ref], (10, 8, 50, 32))
        donor = [*map(int, ref.split(".")), 0]
        edit = {"entry": entry, "item": item, "rows": [[{"donor": donor, "row": 0}]],
                "bands": [(0, 56)], **face}
        art = build.vector_art(edit, served, ["Alder" if "face" in face else "Birch"], 2)
        assert {pixel for pixel in art.get_flattened_data() if pixel[3]} == {references[ref]}, ref


def test_officer_detail_does_not_clip_leading_glow(monkeypatch, tmp_path):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    label = Image.new("RGBA", (128, 80), (255, 255, 255, 255))
    label.paste((255, 0, 0, 1), (0, 0, 1, 80))
    monkeypatch.setattr(build, "pause_button_label", lambda *a: (label.copy(),
                        {"ink": [18, 0, 110, 80], "fill": [18, 110], "baseline": 64}))
    bands = [(42 * row, 42 * (row + 1)) for row in range(4)]
    served = Image.new("RGBA", (512, 168))
    served.paste((255, 255, 255, 255), (3, 134, 60, 158))
    texts = [None, None, None, "Alder controls"]
    monkeypatch.setattr(build, "PAUSE_BUTTON_ROWS", {
        (4080, item, 3): ("Alder controls", 198.062306, 4.981308, 32, None, None)
        for item in (44, 45)
    })
    for item in (44, 45):
        edit = {"entry": 4080, "item": item, "rows": [[{"text": text}] for text in texts],
                "bands": bands, "align": "left"}
        art = build.vector_art(edit, served, texts, 2)
        assert sum(p == (255, 0, 0, 1) for p in art.get_flattened_data()) == 80


@pytest.mark.parametrize("entry,item", [(4060, 2), (4069, 0), (4074, 10)])
def test_help_does_not_recenter_a_shorter_label(monkeypatch, tmp_path, entry, item):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    served = Image.new("RGBA", (64, 32))
    served.paste((38, 38, 38, 255), (20, 9, 50, 23))
    label = Image.new("RGBA", (32, 42))
    label.paste((38, 38, 38, 255), (4, 8, 24, 36))
    monkeypatch.setattr(build, "header_label", lambda *a: (label, {"ink": [4, 8, 24, 36], "baseline": 36}))
    edit = {"entry": entry, "item": item, "style": "help",
            "rows": [[{"text": "Help"}]], "bands": [(0, 32)]}
    assert build.vector_art(edit, served, ["Help"], 2).getbbox() == (80, 18, 100, 46)


def test_notice_growth_does_not_separate_shared_right_edges(monkeypatch, tmp_path):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    served = Image.new("RGBA", (1024, 64))
    served.paste((38, 38, 38, 255), (7, 8, 413, 24))
    served.paste((38, 38, 38, 255), (119, 40, 413, 56))
    def label(text, *args):
        width = 900 if text == "Wide" else 600
        image = Image.new("RGBA", (width + 8, 48))
        image.paste((38, 38, 38, 255), (4, 8, width + 4, 40))
        return image, {"ink": [4, 8, width + 4, 40], "baseline": 40}
    monkeypatch.setattr(build, "header_label", label)
    edit = {"entry": 4074, "item": 10, "style": "help",
            "rows": [[{"text": "Wide"}], [{"text": "Short"}]], "bands": [(0, 32), (32, 64)]}
    art = build.vector_art(edit, served, ["Wide", "Short"], 2)
    rights = [art.crop((0, top, art.width, top + 64)).getbbox()[2] / 2 for top in (0, 64)]
    assert [right - (art.info["minimum"] - 420) for right in rights] == [413, 413]


def test_hires_bank_does_not_serialize_a_sheet_smaller_than_its_quad(monkeypatch, tmp_path):
    import struct
    HIRES_ROW = struct.Struct("<9I")
    from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER

    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    served = Image.new("RGBA", (512, 32))
    served.paste((38, 38, 38, 255), (3, 8, 305, 24))
    pixels = served.tobytes("raw", "BGRA")
    size = G1T_HEADER.size + 4 + G1T_TEXTURE_HEADER.size + len(pixels)
    source = (G1T_HEADER.pack(b"GT1G", b"0000", size, G1T_HEADER.size, 1, 0, 0, 0)
              + struct.pack("<I", 4) + G1T_TEXTURE_HEADER.pack(0x10, 1, 0x59, 0) + pixels)
    entries = tmp_path / "moz-english/entries"
    entries.mkdir(parents=True)
    (entries / "4060.bin").write_bytes(source)
    label = Image.new("RGBA", (692, 48))
    label.paste((38, 38, 38, 255), (1, 8, 689, 40))
    monkeypatch.setattr(build, "header_label", lambda *a: (label, {"ink": [1, 8, 689, 40], "baseline": 40}))
    monkeypatch.setattr(build, "HIRES", [])
    monkeypatch.setattr(build, "DARK", {(4060, 0): "help"})
    monkeypatch.setattr(build, "HIRES_NATIVE_MENU", set())
    monkeypatch.setattr(build, "HELP_QUAD_WIDTH", {(4060, 0): 476})
    mapping = {"faces": {}, "textures": [{"entry": 4060, "item": 0,
                                           "rows": [[{"text": "Help"}]]}]}
    blob = build.hires_bin(entries, None, (mapping, {}))
    row = HIRES_ROW.unpack_from(blob, 4)
    sheet = build.parse_g1t(blob[row[7]:row[7] + row[8]]).textures[0]
    assert (row[2], row[3], row[5] >> 16, sheet.width) == (2, 520, 520, 2048)
    assert (entries / "4060.bin").read_bytes() == source


def test_hires_bank_does_not_shrink_frozen_attribute_samples(monkeypatch, tmp_path):
    import struct

    HIRES_ROW = struct.Struct("<9I")
    from wozmod.formats.textures import G1T_HEADER, G1T_TEXTURE_HEADER

    minima = [76, 48, 52, 72, 56, 68, 92, 52, 76, 80, 64, 80, 72, 68]
    accepted = {item: width for item, width in enumerate(minima, 83)}
    others = {(4080, 6): 32, (4080, 33): 40}
    served = Image.new("RGBA", (128, 32))
    served.paste((38, 38, 38, 255), (0, 6, 16, 23))
    pixels = served.tobytes("raw", "BGRA")
    size = G1T_HEADER.size + 4 + G1T_TEXTURE_HEADER.size + len(pixels)
    g1t = (G1T_HEADER.pack(b"GT1G", b"0000", size, G1T_HEADER.size, 1, 0, 0, 0)
           + struct.pack("<I", 4) + G1T_TEXTURE_HEADER.pack(0x10, 1, 0x57, 0) + pixels)
    member = g1t + bytes((-len(g1t)) % 2048)
    entries = tmp_path / "moz-english/entries"
    entries.mkdir(parents=True)
    sources = {4079: member * 97, 4080: member * 34}
    for entry, payload in sources.items():
        (entries / f"{entry}.bin").write_bytes(payload)
    mapping = {"faces": {}, "textures": [
        {"entry": entry, "item": item, "rows": [[{"text": f"{entry}.{item}"}]]}
        for entry, item in [(4079, item) for item in accepted] + list(others)
    ]}
    monkeypatch.setattr(build, "HIRES", [])
    monkeypatch.setattr(build, "HIRES_NATIVE_MENU", set())
    monkeypatch.setattr(build, "DARK", {
        **{(4079, item): "attributes" for item in accepted},
        **{key: "pause" for key in others},
    })
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    narrower = False

    def label(text, scale, *args):
        entry, item = map(int, text.split("."))
        right = (accepted[item] - 2 if entry == 4079 else others[(entry, item)] - 2)
        if narrower and entry == 4079:
            right -= 12
        width = right * scale
        image = Image.new("RGBA", (width, 64))
        image.paste((item, 38, 38, 255), (0, 6, width, 46))
        return image, {"ink": [0, 6, width, 46], "baseline": 46}

    monkeypatch.setattr(build, "header_label", label)
    before = build.hires_bin(entries, None, (mapping, {}))
    narrower = True
    after = build.hires_bin(entries, None, (mapping, {}))

    def rows(blob):
        count = struct.unpack_from("<I", blob)[0]
        return {record[:2]: (record, blob[4 + index * HIRES_ROW.size:4 + (index + 1) * HIRES_ROW.size],
                            blob[record[7]:record[7] + record[8]])
                for index, record in enumerate(HIRES_ROW.iter_unpack(blob[4:4 + count * HIRES_ROW.size]))}

    old, new = rows(before), rows(after)
    assert set(old) == set(new) == {(4079, item) for item in accepted} | set(others)
    assert [old[4079, item][0][5] >> 16 for item in accepted] == minima
    assert [new[4079, item][0][5] >> 16 for item in accepted] == minima
    for key, (record, header, payload) in old.items():
        if key[0] == 4079:
            assert new[key][0][:7] == record[:7], key
            assert new[key][2] != payload, key
        else:
            assert new[key][1:] == (header, payload), key
    assert {entry: (entries / f"{entry}.bin").read_bytes() for entry in sources} == sources


def test_donor_stem_does_not_ignore_explicit_archives_or_skip_either_member(tmp_path):
    import hashlib
    from wozmod.discs import DiscError
    from wozmod.donors import Donor, DonorFile

    contents = {"linkdata_ens.idx": b"index", "linkdata_ens.lnk": b"archive"}
    donor = Donor("synthetic", "xdvdfs", tuple(
        DonorFile(name, name, len(content), hashlib.sha256(content).hexdigest())
        for name, content in contents.items()
    ))
    stem = tmp_path / "requested"
    for name, content in contents.items():
        stem.with_suffix("." + name.rsplit(".", 1)[1]).write_bytes(content)
    assert build.donor_stem(stem, tmp_path / "absent-disc", donor, tmp_path / "unused") == stem
    for suffix, original in ((".idx", b"index"), (".lnk", b"archive")):
        stem.with_suffix(suffix).write_bytes(b"x" * len(original))
        with pytest.raises(DiscError, match="hash"):
            build.donor_stem(stem, None, donor, tmp_path / "unused")
        stem.with_suffix(suffix).write_bytes(original)


def test_donor_stem_extracts_disc_members_to_the_requested_directory(tmp_path):
    import hashlib
    from wozmod.donors import Donor, DonorFile

    contents = (("linkdata_ens.idx", b"index"), ("linkdata_ens.lnk", b"archive"))
    donor = Donor("synthetic", "xdvdfs", tuple(
        DonorFile(name, name, len(content), hashlib.sha256(content).hexdigest())
        for name, content in contents
    ))
    image = bytearray(36 * 2048)
    image[32 * 2048:32 * 2048 + 20] = b"MICROSOFT*XBOX*MEDIA"
    image[33 * 2048 - 20:33 * 2048] = b"MICROSOFT*XBOX*MEDIA"
    struct.pack_into("<II", image, 32 * 2048 + 20, 33, 2048)
    for index, (name, content) in enumerate(contents):
        encoded = name.encode("ascii")
        record = struct.pack("<HHIIBB", 0, 8 if index == 0 else 0,
                             34 + index, len(content), 0x20, len(encoded)) + encoded
        offset = 33 * 2048 + index * 32
        image[offset:offset + len(record)] = record
        image[(34 + index) * 2048:(34 + index) * 2048 + len(content)] = content
    disc = tmp_path / "disc"
    disc.write_bytes(image)
    destination = tmp_path / "output"
    stem = build.donor_stem(None, disc, donor, destination)
    assert stem == destination / "linkdata_ens"
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == dict(contents)


def test_font_environment_does_not_drop_package_or_inherited_import_paths(monkeypatch, tmp_path):
    import subprocess
    import sys

    inherited = tmp_path / "inherited"
    inherited.mkdir()
    (inherited / "authored_marker.py").write_text("VALUE = 'marker'\n")
    monkeypatch.setenv("PYTHONPATH", str(inherited))
    result = subprocess.run([
        sys.executable, "-S", "-c",
        "from wozmod.art.hires import hires_blob; import authored_marker; print(authored_marker.VALUE)",
    ], cwd=tmp_path, env=build.font_environment(), check=True, capture_output=True, text=True)
    assert result.stdout == "marker\n"


@pytest.mark.parametrize("extra,message", [
    ([], "give --output-dir"),
    (["--entries", "42"], "give --output-dir"),
    (["--output-dir", "out", "--entries", "42"], "give --output-dir"),
    (["--output-dir", "out", "--squeeze"], "--squeeze needs --hires vector"),
    (["--output-dir", "out", "--variant", "help"], "must be NAME=VALUE"),
    (["--output-dir", "out", "--variant", "help=wide", "--variant", "help=wide"], "specified twice"),
    (["--entries", "42", "--preview-dir", "out", "--hires", "vector", "--hires-only"],
     "--hires-only needs --hires and --output-dir"),
])
def test_cli_rejects_invalid_selection_before_opening_inputs(monkeypatch, capsys, tmp_path, extra, message):
    monkeypatch.chdir(tmp_path)
    for name in ("WOZMOD_WO1_DONOR", "WOZMOD_X360_DONOR", "WOZMOD_WO1_ISO", "WOZMOD_X360_ISO"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as error:
        build.main(["--game", str(tmp_path / "absent-game"), *extra])
    assert error.value.code == 2
    assert message in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_officer_banner_does_not_use_wo1_recipe_or_first_x360_member():
    def texture(width, height, colour):
        pixels = bytes((colour[2], colour[1], colour[0], colour[3])) * (width * height)
        dimensions = (width.bit_length() - 1) | ((height.bit_length() - 1) << 4)
        return (struct.pack("<4s4s6I", b"GT1G", b"test", 44 + len(pixels), 32, 1, 0, 0, 0)
                + struct.pack("<I", 4) + struct.pack("<BBHI", 0x10, 1, dimensions, 0) + pixels)

    stock = texture(4, 4, (1, 2, 3, 255))
    package = (stock + bytes(2048 - len(stock))) * 2
    green = (5, 180, 7, 255)
    donors = {496: {0: build.parse_g1t(stock)}, ("wo2", 3): {
        0: build.parse_g1t(texture(8, 4, (190, 10, 15, 255))),
        1: build.parse_g1t(texture(8, 4, green)),
    }}
    edited = build.parse_texture_package(build.edited_entry(
        package, [{"entry": 4063, "item": 1, "texture": 0, "donor": [496, 0, 0]}], donors))
    served = edited.items[1].g1t.textures[0]
    assert (served.width, served.height, served.decode_rgba()) == (8, 4, bytes(green) * 32)
    assert edited.items[0].source == stock


def test_draw_rows_does_not_drop_pieces_after_the_first_value():
    served = Image.new("RGBA", (64, 126))
    for row in range(3):
        served.paste((156, 156, 156, 255), (10, row * 42 + 10, 50, row * 42 + 30))
    mapping = {"faces": {}, "textures": [{"entry": 11, "item": 0, "draw": [
        {"face": "value_dim", "text": text, "into": [0, 8 + 42 * row, 60, 40 + 42 * row]}
        for row, text in enumerate(("Alder", "Birch", "Cedar"))
    ]}]}
    edit = build.rows_edit(11, 0, mapping, served, look="value_dim")
    assert edit["bands"] == [(8, 32), (50, 74), (92, 116)]
    assert build.hires_texts(edit, mapping, {}) == ["Alder", "Birch", "Cedar"]


def test_count_neighbor_uses_its_own_donor_item():
    served = Image.new("RGBA", (64, 32))
    served.paste((205, 205, 205, 255), (4, 4, 40, 24))
    mapping = {"faces": {}, "textures": [
        {"entry": 11, "item": 3, "donor": [7, 10, 0], "count": 2},
    ]}
    edit = build.rows_edit(11, 4, mapping, served, look="confirm_lit")
    assert edit["donor"] == [7, 11, 0]


def test_review_override_does_not_get_lost_or_leak_into_the_next_build(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "REVIEW_VARIANTS", {"help": "per-screen", "camp": "x360"})
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    treatments = {"condensed": (24, (38, 38, 38, 255)),
                  "wide": (40, (57, 57, 57, 255)),
                  "condensed-camp": (32, (57, 57, 57, 255))}

    def rendered_label(text, scale, path, renderer, *options):
        width, colour = treatments[options[options.index("--role") + 1]]
        image = Image.new("RGBA", (width, 32))
        image.paste(colour, (0, 4, width, 24))
        return image, {"ink": [0, 4, width, 24], "baseline": 24}

    monkeypatch.setattr(build, "header_label", rendered_label)
    served = Image.new("RGBA", (128, 32))
    served.paste((38, 38, 38, 255), (3, 7, 50, 25))
    cases = [([], 4069, 0, "condensed"), ([], 4060, 6, "wide"),
             ([], 4060, 12, "condensed-camp"),
             (["help=wide", "camp=wo1"], 4069, 0, "wide"),
             (["help=condensed"], 4060, 6, "condensed"),
             (["help=condensed-camp"], 4069, 0, "condensed-camp"),
             ([], 4069, 0, "condensed")]
    failures = []
    for choices, entry, item, role in cases:
        build.set_review_variants(choices)
        edit = {"entry": entry, "item": item, "style": "help",
                "bands": [(0, 32)], "rows": [[{"text": "Fixture"}]]}
        art = build.vector_art(edit, served, ["Fixture"], 2)
        box = art.getbbox()
        actual = (box[2] - box[0], art.getpixel((box[0], box[1])))
        if actual != treatments[role]:
            failures.append((choices, entry, item, actual, treatments[role]))
    assert not failures, failures


def test_native_ability_bank_does_not_grow_fitting_ink_or_drop_fixed_reserve(monkeypatch, tmp_path):
    source = (struct.pack("<4s4s6I", b"GT1G", b"test", 44 + 128 * 64 * 4, 32, 1, 0, 0, 0)
              + struct.pack("<I", 4) + struct.pack("<BBHI", 0x10, 1, 0x67, 0)
              + bytes(128 * 64 * 4))
    member = source + bytes(-len(source) % 2048)
    entries = tmp_path / "entries"
    entries.mkdir()
    (entries / "4079.bin").write_bytes(member * 77)
    monkeypatch.setattr(build, "HIRES", [])
    monkeypatch.setattr(build, "DARK", {})
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    monkeypatch.setattr(build, "native_menu_layout", lambda edit, donors: {
        "style": "rounded.ability", "role": "yellow", "text": edit["text"],
        "left": 0, "baseline": 28, "sampled_window": [0, 0, 112, 35],
        "alpha_bbox": [0, 2, int(edit["text"]), 28],
    })

    def rendered_label(text, scale, *args):
        width = int(text) * scale
        image = Image.new("RGBA", (width, 64))
        image.paste((220, 180, 40, 255), (0, 2, width, 56))
        return image, {"ink": [0, 2, width, 56], "baseline": 56}

    monkeypatch.setattr(build, "header_label", rendered_label)
    failures = []
    for item, right, minimum in ((76, 112, 0), (76, 113, 116), (69, 126, 144)):
        monkeypatch.setattr(build, "HIRES_NATIVE_MENU", {(4079, item)})
        mapping = {"textures": [{"entry": 4079, "item": item, "texture": 0, "text": str(right)}]}
        bank = build.hires_bin(entries, None, (mapping, {}))
        actual = struct.unpack_from("<9I", bank, 4)[5] >> 16
        if actual != minimum:
            failures.append((item, right, actual, minimum))
    assert not failures, failures
