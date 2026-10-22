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


OFFICIAL = {"donor": "wo2-x360", "kind": "official", "ens": [7, 2, 0], "crop": [0, 0, 512, 42]}
WRONG_CATALOGS = {
    "unknown schema": {"schema": "other"},
    "a field beside textures": {"note": "x"},
    "a texture field no reader takes": {"textures": {"3.2": {"rows": {"7": {"role_variant": "blue"}}, "style": "x"}}},
    "a texture with no rows": {"textures": {"3.2": {"rows": {}}}},
    "a texture not named ENTRY.ITEM": {"textures": {"3": {"rows": {"7": {"role_variant": "blue"}}}}},
    "a row not named by its number": {"textures": {"3.2": {"rows": {"x": {"role_variant": "blue"}}}}},
    "an empty row": {"textures": {"3.2": {"rows": {"7": {}}}}},
    "a misspelt row field": {"textures": {"3.2": {"rows": {"7": {"role_varient": "blue"}}}}},
    "a row field no reader takes": {"textures": {"3.2": {"rows": {"7": {"role_variant": "blue", "alignment": "full"}}}}},
    "a role that is not a name": {"textures": {"3.2": {"rows": {"7": {"role_variant": 1}}}}},
    "a pc_text that is not a word": {"textures": {"3.2": {"rows": {"7": {"pc_text": 1}}}}},
    "an original from an unknown donor": {"textures": {"3.2": {"rows": {"7": {"original": {**OFFICIAL, "donor": "wo1"}}}}}},
    "an original of another kind": {"textures": {"3.2": {"rows": {"7": {"original": {**OFFICIAL, "kind": "guess"}}}}}},
    "an original with a short ens": {"textures": {"3.2": {"rows": {"7": {"original": {**OFFICIAL, "ens": [7, 2]}}}}}},
    "an original with no crop": {"textures": {"3.2": {"rows": {"7": {"original": {
        name: value for name, value in OFFICIAL.items() if name != "crop"}}}}}},
    "a counterpart that names a donor": {"textures": {"3.2": {"rows": {"7": {"original": {
        "kind": "no-official-counterpart", "donor": "wo2-x360"}}}}}},
}


def test_the_catalog_refuses_each_field_no_reader_takes_or_reads_wrongly(tmp_path, monkeypatch):
    """Bug: a misspelt or stale catalog field loads silently, so its row letters with the default role or no donor
    reference instead of failing the build. Each wrong catalog differs from a good one in one place."""
    catalog = tmp_path / "catalog.json"
    monkeypatch.setattr(references, "CATALOG", catalog)
    good = {"schema": "moz-menu-visual-references-v1", "textures": {"3.2": {"rows": {
        "6": {"original": OFFICIAL, "pc_text": "Fixture"}, "7": {"role_variant": "blue"},
        "8": {"original": {"kind": "no-official-counterpart", "donor": None}}}}}}
    loaded = []
    try:
        for name, change in {"good": {}, **WRONG_CATALOGS}.items():
            catalog.write_text(json.dumps({**good, **change}))
            references.load_catalog.cache_clear()
            try:
                references.load_catalog()
                loaded.append(name)
            except ValueError:
                pass
    finally:
        references.load_catalog.cache_clear()
    assert loaded == ["good"]
