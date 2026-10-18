"""Native reference ordinals, crops and catalog coordinates."""

import json
import struct

import pytest

from wozmod.fonts import menu_references as references


def xbox_texture(pixel_format=6, indices=0):
    payload = struct.pack(">HHIHHI", 0xF800, 0x001F, indices, 0x001F, 0xF800, indices)
    return (b"G1TG0050" + struct.pack(">6I", 60, 32, 1, 2, 0, 0)
            + struct.pack(">I", 4) + struct.pack(">8B", 1, pixel_format, 0x32, 0, 0x11, 0x11, 0x10, 0)
            + payload)


@pytest.mark.parametrize("container", ["run", "package"])
def test_x360_unsupported_member_cannot_shift_the_next_reference_ordinal(container):
    first, last = xbox_texture(0), xbox_texture()
    if container == "run":
        data = first + bytes(2048 - len(first)) + last
    else:
        data = (struct.pack(">4I", 0x33B59, 2, 16, 0)
                + struct.pack(">4I", 2, len(first), 6, len(last))
                + first + bytes(4) + last)
    assert list(references.x360_members(data)) == [1]


@pytest.mark.parametrize("damage", ["padding", "truncated"])
def test_x360_damaged_run_cannot_return_a_partial_reference_bank(damage):
    member = xbox_texture()
    data = member + bytes(2048 - len(member)) + member
    if damage == "padding":
        data = data[:100] + bytes((1,)) + data[101:]
    else:
        data = data[:-1]
    with pytest.raises(ValueError, match="padding|exceeds"):
        references.x360_members(data)


def test_default_catalog_cannot_pair_a_row_to_the_equal_archive_ordinal():
    source = references.reference("4060.14", 9)["original"]
    assert (source["ens"], source["crop"]) == ([1, 14, 0], [0, 378, 512, 420])


def test_unpaired_rows_cannot_acquire_an_unrelated_native_reference():
    rows = [("4080.31", 0), ("4080.32", 0), ("4080.33", 0),
            ("4069.23", 0), ("4069.25", 35)]
    sources = [references.reference(texture, row)["original"] for texture, row in rows]
    assert all(source == {"kind": "no-official-counterpart", "donor": None}
               for source in sources)


def test_card_references_cannot_fall_back_to_ordinary_banner_coordinates():
    paired = {64: 105, 86: 118, 87: 119, 88: 120, 89: 121, 90: 122}
    rows = {item: references.reference(f"4095.{item}", 0)["original"]
            for item in [64, *range(79, 96)]}
    actual = {item: (source["ens"], source["crop"])
              for item, source in rows.items() if source["kind"] == "official"}
    assert actual == {item: ([27, source, 0], [0, 0, 196, 266])
                      for item, source in paired.items()}
    assert all(source == {"kind": "no-official-counterpart", "donor": None}
               for item, source in rows.items() if item not in paired)


def test_catalog_row_lookup_cannot_treat_explicit_keys_as_ordinals(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema": "moz-menu-visual-references-v1", "textures": {
        "3.2": {"rows": {"7": {"role_variant": "blue"}}}}}))
    references.load_catalog.cache_clear()
    monkeypatch.setattr(references, "CATALOG", catalog)
    try:
        assert references.reference("3.2", 7) == {"role_variant": "blue"}
        assert references.reference("3.2", 0) is None
    finally:
        references.load_catalog.cache_clear()


def test_unknown_catalog_schema_cannot_supply_recipe_rows(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema": "other", "textures": {
        "3.2": {"rows": {"7": {"role_variant": "blue"}}}}}))
    references.load_catalog.cache_clear()
    monkeypatch.setattr(references, "CATALOG", catalog)
    try:
        with pytest.raises(ValueError, match="unsupported menu visual catalog"):
            references.reference("3.2", 7)
    finally:
        references.load_catalog.cache_clear()


VARIED = 0x1B4EB1E4  # each row of each block a different order of the four DXT1 colours


def donor_folder(tmp_path):
    folder = tmp_path / "wo1-x360"
    folder.mkdir()
    texture = xbox_texture(indices=VARIED)
    (folder / "linkdata_ens.lnk").write_bytes(texture + bytes(2048 - len(texture)))
    (folder / "linkdata_ens.idx").write_bytes(
        struct.pack(">4s3I4I", b"SM4L", 1, 1, 0, 0, 1, len(texture), 0))
    return tmp_path


def test_donor_reader_cannot_read_the_wrong_folder_or_crop_origin(tmp_path):
    """Bug: a reference read from another disc's folder, or cropped from the wrong origin, returns other pixels."""
    want = references.x360_members(xbox_texture(indices=VARIED))[0].textures[0]
    whole = references.Image.frombytes("RGBA", (want.width, want.height), bytes(want.decode_rgba()))
    assert whole.crop((1, 2, 4, 4)).tobytes() not in (whole.crop((0, 2, 3, 4)).tobytes(), whole.crop((1, 0, 4, 2)).tobytes())
    reader = references.DonorReader(donor_folder(tmp_path))
    image = reader.image({"donor": "wo1-x360", "ens": [0, 0, 0], "crop": [1, 2, 4, 4]})
    assert (image.size, image.tobytes()) == ((3, 2), whole.crop((1, 2, 4, 4)).tobytes())


def test_native_crop_cannot_silently_pad_pixels_outside_its_texture(tmp_path):
    reader = references.DonorReader(donor_folder(tmp_path))
    with pytest.raises(ValueError, match="crop exceeds"):
        reader.image({"donor": "wo1-x360", "ens": [0, 0, 0], "crop": [0, 0, 9, 4]})


def test_missing_counterpart_cannot_try_to_read_a_donor_archive(tmp_path):
    reader = references.DonorReader(tmp_path)
    with pytest.raises(ValueError, match="no official counterpart"):
        reader.image({"kind": "no-official-counterpart"})
