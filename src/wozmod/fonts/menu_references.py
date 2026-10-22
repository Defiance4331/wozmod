"""Native donor texture references."""
from functools import lru_cache
import json
import struct

from wozmod import resources
from wozmod.donors import check_disc_names

from wozmod.formats.textures import (
    TextureCodecError, x360_g1t,
)

REPO = resources.root()
CATALOG = REPO / "data/art/menu_visuals.json"
ROW_FIELDS = {"original", "pc_text", "role_variant"}
NO_COUNTERPART = {"kind": "no-official-counterpart", "donor": None}
OFFICIAL_DONORS = {"wo2-x360", "wo1-x360"}


def _integers(value, count: int) -> bool:
    return isinstance(value, list) and len(value) == count and all(type(n) is int and n >= 0 for n in value)


def _check_row(where: str, row) -> None:
    if not isinstance(row, dict) or not row or not set(row) <= ROW_FIELDS:
        raise ValueError(f"menu visual catalog row {where}: a non-empty object of {sorted(ROW_FIELDS)}")
    if "role_variant" in row and not isinstance(row["role_variant"], str):
        raise ValueError(f"menu visual catalog row {where}: role_variant must be a string")
    if "pc_text" in row and not isinstance(row["pc_text"], (str, dict)):
        raise ValueError(f"menu visual catalog row {where}: pc_text must be a word or a stored form")
    original = row.get("original")
    if original is None or original == NO_COUNTERPART:
        return
    if (not isinstance(original, dict) or set(original) != {"donor", "kind", "ens", "crop"}
            or original["donor"] not in OFFICIAL_DONORS or original["kind"] != "official"
            or not _integers(original["ens"], 3) or not _integers(original["crop"], 4)):
        raise ValueError(f"menu visual catalog row {where}: original must be {NO_COUNTERPART} or an official "
                         "donor, kind, ens [entry, member, texture] and crop [left, top, right, bottom]")


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """Read the recipe catalog, refusing any field no reader takes. Its pc_text words stay stored forms (see
    wozmod.art.words)."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "moz-menu-visual-references-v1":
        raise ValueError(f"unsupported menu visual catalog: {CATALOG}")
    if set(data) != {"schema", "textures"} or not isinstance(data["textures"], dict):
        raise ValueError(f"menu visual catalog holds schema and textures only: {CATALOG}")
    check_disc_names(data, f"menu visual catalog {CATALOG}")
    for texture, record in data["textures"].items():
        entry, _, item = texture.partition(".")
        if not (entry.isdecimal() and item.isdecimal()):
            raise ValueError(f"menu visual catalog texture {texture!r}: name it ENTRY.ITEM")
        if not isinstance(record, dict) or set(record) != {"rows"} or not isinstance(record["rows"], dict) \
                or not record["rows"]:
            raise ValueError(f"menu visual catalog texture {texture}: holds non-empty rows only")
        for row, value in record["rows"].items():
            if not row.isdecimal():
                raise ValueError(f"menu visual catalog texture {texture}: row {row!r} is not a number")
            _check_row(f"{texture}/{row}", value)
    return data["textures"]

def reference(texture: str, row: int) -> dict | None:
    """Return the explicitly keyed row reference."""
    return load_catalog().get(texture, {}).get("rows", {}).get(str(row))

def x360_members(data: bytes) -> dict:
    """Read native X360 members without changing their ordinals."""
    if data[:4] == b"G1TG":
        members, position, item = {}, 0, 0
        while position < len(data):
            if data[position:position + 4] != b"G1TG":
                if not any(data[position:]):
                    break
                raise ValueError(f"unexpected Xbox G1TG run bytes at {position}")
            if position + 12 > len(data):
                raise ValueError("truncated Xbox G1TG run header")
            size = struct.unpack_from(">I", data, position + 8)[0]
            if size < 32 or position + size > len(data):
                raise ValueError(f"Xbox G1TG run member {item} exceeds input")
            try:
                members[item] = x360_g1t(data[position:position + size])
            except TextureCodecError as error:

                if not str(error).startswith("unsupported Xbox 360 texture format"):
                    raise
            aligned = (position + size + 2047) // 2048 * 2048
            if any(data[position + size:min(aligned, len(data))]):
                raise ValueError(f"nonzero Xbox G1TG run padding after {item}")
            position, item = aligned, item + 1
        return members
    if len(data) < 16:
        raise ValueError("truncated Xbox texture package")
    magic, count, table, _ = struct.unpack_from(">4I", data)
    if magic != 0x33B59 or table < 16 or table + count * 8 > len(data):
        raise ValueError("invalid Xbox texture package table")
    members = {}
    for item in range(count):
        offset, size = struct.unpack_from(">II", data, table + 8 * item)
        start = offset * 16
        if start + size > len(data):
            raise ValueError(f"Xbox texture package member {item} exceeds input")
        if data[start:start + 4] == b"G1TG":
            try:
                members[item] = x360_g1t(data[start:start + size])
            except TextureCodecError as error:
                if not str(error).startswith("unsupported Xbox 360 texture format"):
                    raise
    return members
