"""Native donor texture references."""
from functools import lru_cache
import json
from pathlib import Path
import struct

from PIL import Image

from wozmod import resources

from wozmod.donors import WO1_X360, X360
from wozmod.formats.linkdata import LZP2_SIGNATURE, WO1_LZP2_SIGNATURE, parse_lzp2, parse_wo1_index
from wozmod.formats.textures import (
    TextureCodecError, x360_g1t,
)

REPO = resources.root()
CATALOG = REPO / "data/art/menu_visuals.json"

@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """Read the recipe catalog. Its pc_text words stay stored forms (see wozmod.art.words)."""
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    if data.get("schema") != "moz-menu-visual-references-v1":
        raise ValueError(f"unsupported menu visual catalog: {CATALOG}")
    return data["textures"]

def reference(texture: str, row: int) -> dict | None:
    """Return the explicitly keyed row reference."""
    return load_catalog().get(texture, {}).get("rows", {}).get(str(row))

def visual_role(texture: str, row: int) -> str | None:
    """Return optional role metadata."""
    record = reference(texture, row)
    return record.get("semantic_role") if record else None

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

class DonorReader:
    """Read donor archive textures without extracting them."""

    def __init__(self, root: Path = REPO / "work/donors") -> None:
        self.root = root
        self._indices = {}
        self._members = {}

    def members(self, donor: str, entry: int) -> dict:
        """Read one donor archive entry."""
        key = donor, entry
        if key in self._members:
            return self._members[key]
        if donor not in ("wo1-x360", "x360"):
            raise ValueError(f"unsupported native reference donor: {donor}")
        folder = self.root / {"wo1-x360": WO1_X360, "x360": X360}[donor].name
        archive = folder / "linkdata_ens.lnk"
        if donor not in self._indices:
            self._indices[donor] = parse_wo1_index(
                (folder / "linkdata_ens.idx").read_bytes(), data_size=archive.stat().st_size)
        record = self._indices[donor][entry]
        with archive.open("rb") as stream:
            stream.seek(record.offset_sectors * 2048)
            data = stream.read(record.stored_bytes)
        if data[:4] == b"LZP2":
            signature = LZP2_SIGNATURE if donor == "x360" else WO1_LZP2_SIGNATURE
            data = parse_lzp2(data, signature).decoded
        members = x360_members(data)
        self._members[key] = members
        return members

    def image(self, source: dict) -> Image.Image:
        """Decode and crop a native reference."""
        if source.get("kind") == "no-official-counterpart":
            raise ValueError("no official counterpart: no native donor image exists")
        entry, item, number = source["ens"]
        texture = self.members(source["donor"], entry)[item].textures[number]
        image = Image.frombytes("RGBA", (texture.width, texture.height), texture.decode_rgba())
        box = source.get("crop", [0, 0, image.width, image.height])
        if (len(box) != 4 or any(type(n) is not int for n in box) or
                not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height)):
            raise ValueError(f"native reference crop exceeds ENS {source['ens']}: {box}")
        return image.crop(box)
