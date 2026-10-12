"""G1T containers, texture packages and DXT codecs."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from wozmod import native

G1T_MAGIC = b"GT1G"
G1T_HEADER = struct.Struct("<4s4sIIIIII")
G1T_TEXTURE_HEADER = struct.Struct("<BBHI")
TEXTURE_PACKAGE_MAGIC = 0x00033B59
TEXTURE_PACKAGE_HEADER = struct.Struct("<IIII")
TEXTURE_PACKAGE_RECORD = struct.Struct("<II")
TEXTURE_PACKAGE_ALIGNMENT = 16
G1T_RUN_ALIGNMENT = 0x800
PIXEL_FORMAT_BGRA8 = 0x01
PIXEL_FORMAT_DXT1 = 0x06
PIXEL_FORMAT_DXT5 = 0x08
SUPPORTED_PIXEL_FORMATS = frozenset(
    (PIXEL_FORMAT_BGRA8, PIXEL_FORMAT_DXT1, PIXEL_FORMAT_DXT5)
)


class TextureCodecError(ValueError):
    """Invalid or unsupported texture bytes."""


def _align(value: int, alignment: int = TEXTURE_PACKAGE_ALIGNMENT) -> int:
    return (value + alignment - 1) // alignment * alignment


def _mip_payload_bytes(pixel_format: int, width: int, height: int) -> int:
    if pixel_format == PIXEL_FORMAT_BGRA8:
        return width * height * 4
    if pixel_format == PIXEL_FORMAT_DXT1:
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 8
    if pixel_format == PIXEL_FORMAT_DXT5:
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 16
    raise TextureCodecError(f"unsupported G1T pixel format: 0x{pixel_format:02x}")


def _expected_payload_bytes(
    pixel_format: int, width: int, height: int, mipmap_count: int
) -> int:
    return sum(
        _mip_payload_bytes(
            pixel_format,
            max(1, width >> level),
            max(1, height >> level),
        )
        for level in range(mipmap_count)
    )


@dataclass(frozen=True)
class G1tTexture:
    """A texture and its byte span."""

    ordinal: int
    offset: int
    end: int
    mipmap_count: int
    pixel_format: int
    width: int
    height: int
    flags: int
    payload: bytes = field(repr=False)

    @property
    def pixel_format_name(self) -> str:
        """Return the pixel format name."""

        return {
            PIXEL_FORMAT_BGRA8: "bgra8",
            PIXEL_FORMAT_DXT1: "dxt1",
            PIXEL_FORMAT_DXT5: "dxt5",
        }[self.pixel_format]

    def decode_rgba(self) -> bytes:
        """Decode the top mip to RGBA8."""

        top_bytes = _mip_payload_bytes(self.pixel_format, self.width, self.height)
        payload = self.payload[:top_bytes]
        if self.pixel_format == PIXEL_FORMAT_BGRA8:
            return _swap_red_blue(payload)
        if self.pixel_format == PIXEL_FORMAT_DXT1:
            return _decode_dxt(payload, self.width, self.height, alpha=False)
        if self.pixel_format == PIXEL_FORMAT_DXT5:
            return _decode_dxt(payload, self.width, self.height, alpha=True)
        raise AssertionError("unreachable pixel format")

    def encode_rgba(self, rgba: bytes) -> bytes:
        """Encode the top mip and preserve lower mips."""

        rgba = bytes(rgba)
        expected = self.width * self.height * 4
        if len(rgba) != expected:
            raise TextureCodecError(
                f"RGBA replacement needs {expected} bytes, got {len(rgba)}"
            )
        if rgba == self.decode_rgba():
            return self.payload
        top_bytes = _mip_payload_bytes(self.pixel_format, self.width, self.height)
        top = _encode_top(self.pixel_format, rgba, self.width, self.height, self)
        if len(top) != top_bytes:
            raise AssertionError("texture encoder changed the top-level span")
        return top + self.payload[top_bytes:]

    def palette_modes(self) -> dict[str, int]:
        """Count DXT palette branches."""

        modes = {
            "dxt1-four-color": 0,
            "dxt1-three-color-transparent": 0,
            "dxt5-alpha-eight": 0,
            "dxt5-alpha-six-plus-extremes": 0,
        }
        if self.pixel_format == PIXEL_FORMAT_DXT1:
            top = _mip_payload_bytes(self.pixel_format, self.width, self.height)
            for offset in range(0, top, 8):
                color0, color1 = struct.unpack_from("<HH", self.payload, offset)
                key = (
                    "dxt1-four-color"
                    if color0 > color1
                    else "dxt1-three-color-transparent"
                )
                modes[key] += 1
        elif self.pixel_format == PIXEL_FORMAT_DXT5:
            top = _mip_payload_bytes(self.pixel_format, self.width, self.height)
            for offset in range(0, top, 16):
                key = (
                    "dxt5-alpha-eight"
                    if self.payload[offset] > self.payload[offset + 1]
                    else "dxt5-alpha-six-plus-extremes"
                )
                modes[key] += 1
        return {key: value for key, value in modes.items() if value}


@dataclass(frozen=True)
class G1tFile:
    """A losslessly parsed G1T container."""

    source: bytes = field(repr=False)
    identity: bytes
    table_offset: int
    field_20: int
    field_24: int
    field_28: int
    texture_offsets: tuple[int, ...]
    textures: tuple[G1tTexture, ...]

    def encode(self) -> bytes:
        """Return the original bytes."""

        return self.source

    def with_texture_payload(self, texture_index: int, payload: bytes) -> G1tFile:
        """Replace a payload without changing its size."""

        if texture_index < 0 or texture_index >= len(self.textures):
            raise TextureCodecError(f"G1T texture is out of range: {texture_index}")
        texture = self.textures[texture_index]
        payload = bytes(payload)
        if len(payload) != len(texture.payload):
            raise TextureCodecError("replacement texture payload size differs")
        if payload == texture.payload:
            return self
        changed = bytearray(self.source)
        start = texture.offset + G1T_TEXTURE_HEADER.size
        changed[start : start + len(payload)] = payload
        return parse_g1t(bytes(changed))

    def with_texture_rgba(self, texture_index: int, rgba: bytes) -> G1tFile:
        """Replace the top mip from RGBA8 pixels."""

        texture = self.textures[texture_index]
        return self.with_texture_payload(texture_index, texture.encode_rgba(rgba))

    def with_texture_resized(
        self, texture_index: int, width: int, height: int, rgba: bytes, pixel_format: int | None = None
    ) -> G1tFile:
        """Resize a single-mip texture, optionally in another pixel format, and move later textures."""

        texture = self.textures[texture_index]
        pixel_format = texture.pixel_format if pixel_format is None else pixel_format
        if pixel_format not in SUPPORTED_PIXEL_FORMATS:
            raise TextureCodecError(f"unsupported pixel format {pixel_format}")
        if texture.mipmap_count != 1:
            raise TextureCodecError("only single-mip textures can be resized")
        if width & (width - 1) or height & (height - 1) or len(rgba) != width * height * 4:
            raise TextureCodecError("resized texture needs power-of-two RGBA pixels")
        header = G1T_TEXTURE_HEADER.pack(
            1 << 4,
            pixel_format,
            (width.bit_length() - 1) | (height.bit_length() - 1) << 4,
            texture.flags,
        )
        span = header + _encode_top(pixel_format, bytes(rgba), width, height, texture)
        delta = len(span) - (texture.end - texture.offset)
        changed = bytearray(
            self.source[: texture.offset] + span + self.source[texture.end :]
        )
        offsets = [o + delta * (i > texture_index) for i, o in enumerate(self.texture_offsets)]
        struct.pack_into(f"<{len(offsets)}I", changed, self.table_offset, *offsets)
        struct.pack_into("<I", changed, 8, len(changed))
        return parse_g1t(bytes(changed))


@dataclass(frozen=True)
class TexturePackageItem:
    """An item and its aligned byte span."""

    ordinal: int
    identifier: int  # the record's first word: the item offset / 16 in game packages
    offset: int
    source: bytes = field(repr=False)
    padding: bytes = field(repr=False)
    g1t: G1tFile | None


@dataclass(frozen=True)
class TexturePackage:
    """A standalone G1T, aligned package or G1T run."""

    source: bytes = field(repr=False)
    standalone: bool
    items: tuple[TexturePackageItem, ...]
    run: bool = False

    def encode(self) -> bytes:
        """Return the original bytes."""

        return self.source

    def with_g1t(self, item_index: int, g1t: G1tFile) -> TexturePackage:
        """Replace an item and repack offsets when its size changes."""

        if item_index < 0 or item_index >= len(self.items):
            raise TextureCodecError(f"texture package item is out of range: {item_index}")
        item = self.items[item_index]
        encoded = g1t.encode()
        if item.g1t is None:
            raise TextureCodecError("texture package item is not a G1T")
        if encoded == item.source:
            return self
        if self.standalone:
            return parse_texture_package(encoded)
        if self.run:
            return parse_texture_package(b"".join(
                data + bytes(_align(len(data), G1T_RUN_ALIGNMENT) - len(data))
                for data in (encoded if other.ordinal == item_index else other.source for other in self.items)
            ))
        if len(encoded) != len(item.source):
            # A record's first word is its item's offset in 16-byte units, so every
            # later record moves with the resized item.
            head, body = bytearray(self.source[: TEXTURE_PACKAGE_HEADER.size]), bytearray()
            position = _align(TEXTURE_PACKAGE_HEADER.size + len(self.items) * TEXTURE_PACKAGE_RECORD.size)
            for other in self.items:
                data = encoded if other.ordinal == item_index else other.source
                head += TEXTURE_PACKAGE_RECORD.pack(position // TEXTURE_PACKAGE_ALIGNMENT, len(data))
                body += data + bytes(_align(len(data)) - len(data))
                position += _align(len(data))
            return parse_texture_package(bytes(head + bytes(_align(len(head)) - len(head)) + body))
        changed = bytearray(self.source)
        changed[item.offset : item.offset + len(encoded)] = encoded
        return parse_texture_package(bytes(changed))


def parse_g1t(data: bytes) -> G1tFile:
    """Parse a G1T without normalization."""

    source = bytes(data)
    if len(source) < G1T_HEADER.size:
        raise TextureCodecError("G1T header is truncated")
    magic, identity, file_size, table_offset, count, field_20, field_24, field_28 = (
        G1T_HEADER.unpack_from(source)
    )
    if magic != G1T_MAGIC:
        raise TextureCodecError(f"unsupported G1T magic: {magic!r}")
    if file_size != len(source):
        raise TextureCodecError("G1T declared size does not match its input")
    table_end = table_offset + count * 4
    if table_offset < G1T_HEADER.size or table_end > len(source):
        raise TextureCodecError("G1T texture offset table is outside the input")
    offsets = struct.unpack_from(f"<{count}I", source, table_offset)
    starts = tuple(table_offset + offset for offset in offsets)
    if starts and starts[0] < table_end:
        raise TextureCodecError("G1T first texture overlaps its offset table")
    if any(left >= right for left, right in zip(starts, starts[1:])):
        raise TextureCodecError("G1T texture offsets are not strictly increasing")

    textures: list[G1tTexture] = []
    for ordinal, start in enumerate(starts):
        end = starts[ordinal + 1] if ordinal + 1 < count else len(source)
        if start + G1T_TEXTURE_HEADER.size > end:
            raise TextureCodecError(f"G1T texture {ordinal} header is truncated")
        mipmaps, pixel_format, dimensions, flags = G1T_TEXTURE_HEADER.unpack_from(
            source, start
        )
        mipmap_count = mipmaps >> 4
        width = 1 << (dimensions & 0x0F)
        height = 1 << (dimensions >> 4)
        if mipmaps & 0x0F:
            raise TextureCodecError(
                f"G1T texture {ordinal} has unsupported mipmap flags"
            )
        if mipmap_count < 1:
            raise TextureCodecError(f"G1T texture {ordinal} has no mip levels")
        if pixel_format not in SUPPORTED_PIXEL_FORMATS:
            raise TextureCodecError(
                f"G1T texture {ordinal} has unsupported pixel format "
                f"0x{pixel_format:02x}"
            )
        payload = source[start + G1T_TEXTURE_HEADER.size : end]
        expected = _expected_payload_bytes(
            pixel_format, width, height, mipmap_count
        )
        if len(payload) != expected:
            raise TextureCodecError(
                f"G1T texture {ordinal} payload has {len(payload)} bytes, "
                f"expected {expected}"
            )
        textures.append(
            G1tTexture(
                ordinal=ordinal,
                offset=start,
                end=end,
                mipmap_count=mipmap_count,
                pixel_format=pixel_format,
                width=width,
                height=height,
                flags=flags,
                payload=payload,
            )
        )
    return G1tFile(
        source=source,
        identity=identity,
        table_offset=table_offset,
        field_20=field_20,
        field_24=field_24,
        field_28=field_28,
        texture_offsets=tuple(offsets),
        textures=tuple(textures),
    )


def parse_texture_package(data: bytes) -> TexturePackage:
    """Parse a standalone G1T, aligned package or G1T run."""

    source = bytes(data)
    if source.startswith(G1T_MAGIC) and G1T_HEADER.unpack_from(source)[2] < len(source):
        return _parse_g1t_run(source)
    if source.startswith(G1T_MAGIC):
        g1t = parse_g1t(source)
        return TexturePackage(
            source=source,
            standalone=True,
            items=(
                TexturePackageItem(
                    ordinal=0,
                    identifier=0,
                    offset=0,
                    source=source,
                    padding=b"",
                    g1t=g1t,
                ),
            ),
        )
    if len(source) < TEXTURE_PACKAGE_HEADER.size:
        raise TextureCodecError("texture package header is truncated")
    magic, count, alignment, reserved = TEXTURE_PACKAGE_HEADER.unpack_from(source)
    if magic != TEXTURE_PACKAGE_MAGIC:
        raise TextureCodecError(f"unsupported texture package magic: 0x{magic:08x}")
    if alignment != TEXTURE_PACKAGE_ALIGNMENT or reserved != 0:
        raise TextureCodecError("texture package header fields are unsupported")
    table_end = TEXTURE_PACKAGE_HEADER.size + count * TEXTURE_PACKAGE_RECORD.size
    if table_end > len(source):
        raise TextureCodecError("texture package record table is truncated")
    position = _align(table_end)
    if any(source[table_end:position]):
        raise TextureCodecError("texture package table padding is not zero")
    items: list[TexturePackageItem] = []
    for ordinal in range(count):
        identifier, size = TEXTURE_PACKAGE_RECORD.unpack_from(
            source, TEXTURE_PACKAGE_HEADER.size + ordinal * 8
        )
        end = position + size
        padded_end = _align(end)
        if padded_end > len(source):
            raise TextureCodecError(f"texture package item {ordinal} is truncated")
        item_source = source[position:end]
        padding = source[end:padded_end]
        if any(padding):
            raise TextureCodecError(
                f"texture package item {ordinal} padding is not zero"
            )
        g1t = parse_g1t(item_source) if item_source else None
        items.append(
            TexturePackageItem(
                ordinal=ordinal,
                identifier=identifier,
                offset=position,
                source=item_source,
                padding=padding,
                g1t=g1t,
            )
        )
        position = padded_end
    if position != len(source):
        raise TextureCodecError("texture package has unreferenced trailing bytes")
    return TexturePackage(source=source, standalone=False, items=tuple(items))


def _parse_g1t_run(source: bytes) -> TexturePackage:
    items: list[TexturePackageItem] = []
    position = 0
    while position < len(source):
        if len(source) - position < G1T_HEADER.size or not source.startswith(G1T_MAGIC, position):
            raise TextureCodecError(f"G1T run item {len(items)} is not a G1T")
        end = position + G1T_HEADER.unpack_from(source, position)[2]
        padded_end = _align(end, G1T_RUN_ALIGNMENT)
        if padded_end > len(source):
            raise TextureCodecError(f"G1T run item {len(items)} is truncated")
        if any(source[end:padded_end]):
            raise TextureCodecError(f"G1T run item {len(items)} padding is not zero")
        items.append(TexturePackageItem(
            ordinal=len(items), identifier=0, offset=position, source=source[position:end],
            padding=source[end:padded_end], g1t=parse_g1t(source[position:end]),
        ))
        position = padded_end
    return TexturePackage(source=source, standalone=False, items=tuple(items), run=True)


def _swap_red_blue(pixels: bytes) -> bytes:
    """BGRA8 <-> RGBA8."""
    swapped = bytearray(pixels)
    swapped[0::4], swapped[2::4] = pixels[2::4], pixels[0::4]
    return bytes(swapped)


def _encode_top(pixel_format: int, rgba: bytes, width: int, height: int, base: G1tTexture | None = None) -> bytes:
    """Encode the top mip. Every block of a DXT texture that `base` already shows the same pixels in, at the
    same block position, keeps `base`'s compressed bytes, so only edited blocks pass through the encoder."""
    if pixel_format == PIXEL_FORMAT_BGRA8:
        return _swap_red_blue(rgba)
    if pixel_format not in (PIXEL_FORMAT_DXT1, PIXEL_FORMAT_DXT5):
        raise AssertionError("unreachable pixel format")
    if base is not None and base.pixel_format != pixel_format:
        base = None
    return _encode_dxt(pixel_format, rgba, width, height, base)


def _encode_dxt(pixel_format: int, rgba: bytes, width: int, height: int, base: G1tTexture | None) -> bytes:
    if pixel_format == PIXEL_FORMAT_DXT5 and native.module:
        if base is None:
            return native.module.dxt5_encode(rgba, width, height)
        return native.module.dxt5_encode_edit(
            rgba, width, height, base.decode_rgba(), base.payload, base.width, base.height)
    return _encode_dxt_python(pixel_format, rgba, width, height, base)


def _encode_dxt_python(pixel_format: int, rgba: bytes, width: int, height: int, base: G1tTexture | None) -> bytes:
    encode, size = (_dxt5_block, 16) if pixel_format == PIXEL_FORMAT_DXT5 else (_dxt1_block, 8)
    if base is None:
        return b"".join(map(encode, _blocks(rgba, width, height)))
    kept = list(_blocks(base.decode_rgba(), base.width, base.height))
    whole_columns, whole_rows = base.width // 4, base.height // 4  # edge blocks repeat pixels: never copied
    columns, out = max(1, (base.width + 3) // 4), []
    for index, block in enumerate(_blocks(rgba, width, height)):
        row, column = divmod(index, max(1, (width + 3) // 4))
        if column < whole_columns and row < whole_rows and kept[row * columns + column] == block:
            at = (row * columns + column) * size
            out.append(base.payload[at:at + size])
        else:
            out.append(encode(block))
    return b"".join(out)


def _rgb565_to_rgba(value: int) -> tuple[int, int, int, int]:
    red = (value >> 11) & 0x1F
    green = (value >> 5) & 0x3F
    blue = value & 0x1F
    return (
        (red << 3) | (red >> 2),
        (green << 2) | (green >> 4),
        (blue << 3) | (blue >> 2),
        255,
    )


def _rgba_to_rgb565(pixel: tuple[int, int, int, int]) -> int:
    red, green, blue, _alpha = pixel
    return ((red * 31 + 127) // 255 << 11) | (
        (green * 63 + 127) // 255 << 5
    ) | ((blue * 31 + 127) // 255)


def _mix(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
    left_weight: int,
    right_weight: int,
    divisor: int,
) -> tuple[int, int, int, int]:
    return tuple(
        (left[channel] * left_weight + right[channel] * right_weight) // divisor
        for channel in range(4)
    )


def _color_palette(
    color0: int, color1: int, *, force_four: bool
) -> tuple[tuple[int, int, int, int], ...]:
    first = _rgb565_to_rgba(color0)
    second = _rgb565_to_rgba(color1)
    if force_four or color0 > color1:
        return (first, second, _mix(first, second, 2, 1, 3), _mix(first, second, 1, 2, 3))
    return (first, second, _mix(first, second, 1, 1, 2), (0, 0, 0, 0))


def _alpha_palette(alpha0: int, alpha1: int) -> tuple[int, ...]:
    if alpha0 > alpha1:
        return (alpha0, alpha1) + tuple(
            ((7 - index) * alpha0 + index * alpha1) // 7
            for index in range(1, 7)
        )
    return (alpha0, alpha1) + tuple(
        ((5 - index) * alpha0 + index * alpha1) // 5
        for index in range(1, 5)
    ) + (0, 255)


def _decode_dxt(payload: bytes, width: int, height: int, *, alpha: bool) -> bytes:
    from PIL import Image

    block_bytes = 16 if alpha else 8
    if len(payload) != max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_bytes:
        raise TextureCodecError("DXT payload size does not match its geometry")
    # Pillow's BC1/BC3 decoder uses the same 565 expansion and integer palette rounding as the encoder here.
    return Image.frombytes("RGBA", (width, height), payload, "bcn", 3 if alpha else 1).tobytes()


def _blocks(rgba: bytes, width: int, height: int) -> Iterator[bytes]:
    """Yield every 4x4 block's 64 RGBA bytes in block order, repeating the last row and column at the edges."""
    for block_y in range(max(1, (height + 3) // 4)):
        rows = [min(height - 1, block_y * 4 + local_y) * width * 4 for local_y in range(4)]
        for block_x in range(max(1, (width + 3) // 4)):
            x = block_x * 16
            if x + 16 <= width * 4:
                yield (rgba[rows[0] + x:rows[0] + x + 16] + rgba[rows[1] + x:rows[1] + x + 16]
                       + rgba[rows[2] + x:rows[2] + x + 16] + rgba[rows[3] + x:rows[3] + x + 16])
            else:
                columns = [min(width - 1, block_x * 4 + local_x) * 4 for local_x in range(4)]
                yield b"".join(rgba[row + column:row + column + 4] for row in rows for column in columns)


def _pixels(block: bytes) -> tuple[tuple[int, int, int, int], ...]:
    channels = iter(block)
    return tuple(zip(channels, channels, channels, channels))


def _color_endpoints(
    pixels: tuple[tuple[int, int, int, int], ...], *, transparent: bool, visible: int = 128
) -> tuple[int, int]:
    opaque = [pixel for pixel in pixels if pixel[3] >= visible] or pixels
    # The darkest and brightest by channel sum, then by (red, green, blue).
    keys = [(red + green + blue, red, green, blue) for red, green, blue, _alpha in opaque]
    _, *low = min(keys)
    _, *high = max(keys)
    low565 = _rgba_to_rgb565((*low, 255))
    high565 = _rgba_to_rgb565((*high, 255))
    if transparent:
        color0, color1 = min(low565, high565), max(low565, high565)
    else:
        color0, color1 = max(low565, high565), min(low565, high565)
        if color0 == color1:
            if color0 < 0xFFFF:
                color0 += 1
            else:
                color1 -= 1
    return color0, color1


def _nearest(red: int, green: int, blue: int, palette) -> tuple[int, int]:
    """The nearest palette colour's (index, squared distance), the lowest index on a tie."""
    distances = [(red - r) ** 2 + (green - g) ** 2 + (blue - b) ** 2 for r, g, b, _a in palette]
    best = min(distances)
    return distances.index(best), best


def _visible_error(
    pixels: tuple[tuple[int, int, int, int], ...], endpoints: tuple[int, int]
) -> int:
    """Sum squared color error weighted by squared alpha."""
    palette = _color_palette(*endpoints, force_four=True)
    return sum(alpha * alpha * _nearest(red, green, blue, palette)[1]
               for red, green, blue, alpha in pixels if alpha)


def _baseline_endpoints(
    pixels: tuple[tuple[int, int, int, int], ...], *, allow_transparency: bool
) -> tuple[int, int]:
    """The quick endpoints (darkest and brightest pixel) that the search starts from; it never ends worse."""
    transparent = allow_transparency and any(pixel[3] < 128 for pixel in pixels)
    color0, color1 = _color_endpoints(pixels, transparent=transparent)
    if not allow_transparency:  # DXT5 endpoint candidates include faint pixels.
        lit = _color_endpoints(pixels, transparent=False, visible=1)
        if lit != (color0, color1) and _visible_error(pixels, lit) < _visible_error(pixels, (color0, color1)):
            color0, color1 = lit
    return color0, color1


@lru_cache(maxsize=1 << 16)
def _palette_rgb(color0: int, color1: int, transparent: bool) -> tuple[tuple[int, int, int], ...]:
    """The palette colours a block with these endpoints shows (three colours before the transparent one)."""
    first, second = _rgb565_to_rgba(color0)[:3], _rgb565_to_rgba(color1)[:3]
    if transparent:
        return first, second, tuple((a + b) // 2 for a, b in zip(first, second))
    return (first, second, tuple((2 * a + b) // 3 for a, b in zip(first, second)),
            tuple((a + 2 * b) // 3 for a, b in zip(first, second)))


def _rounded_quotient(numerator: int, denominator: int) -> int:
    """numerator / denominator rounded to nearest, halves up; the denominator is positive."""
    return (2 * numerator + denominator) // (2 * denominator)


def _color_error(colors, color0: int, color1: int, transparent: bool) -> int:
    palette = _palette_rgb(color0, color1, transparent)
    return sum(weight * min((red - r) ** 2 + (green - g) ** 2 + (blue - b) ** 2 for r, g, b in palette)
               for red, green, blue, weight in colors)


def _least_squares_endpoints(colors, color0: int, color1: int, transparent: bool) -> tuple[int, int] | None:
    """Endpoints that best fit the pixels each palette index claims now; None when they cannot move."""
    palette = _palette_rgb(color0, color1, transparent)
    shares, divisor = (((2, 0), (0, 2), (1, 1)), 2) if transparent else (((3, 0), (0, 3), (2, 1), (1, 2)), 3)
    aa = ab = bb = 0
    xa, xb = [0, 0, 0], [0, 0, 0]
    for red, green, blue, weight in colors:
        distances = [(red - r) ** 2 + (green - g) ** 2 + (blue - b) ** 2 for r, g, b in palette]
        first, second = shares[distances.index(min(distances))]
        aa += weight * first * first
        ab += weight * first * second
        bb += weight * second * second
        for channel, value in enumerate((red, green, blue)):
            xa[channel] += weight * first * value
            xb[channel] += weight * second * value
    determinant = aa * bb - ab * ab
    if determinant == 0:
        return None
    ends = []
    for numbers in ((xa, xb, bb, ab), (xb, xa, aa, ab)):
        near, far, own, cross = numbers
        ends.append(_rgba_to_rgb565((*(min(255, max(0, _rounded_quotient(
            divisor * (near[channel] * own - far[channel] * cross), determinant)))
            for channel in range(3)), 255)))
    return ends[0], ends[1]


_RGB565_STEPS = tuple((1 << shift, limit, shift) for shift, limit in ((11, 31), (5, 63), (0, 31)))


def _color_neighbours(color0: int, color1: int):
    """Every endpoint pair one step of one channel away, in a fixed order."""
    for which in (0, 1):
        value = (color0, color1)[which]
        for step, limit, shift in _RGB565_STEPS:
            field = value >> shift & limit
            for delta in (-1, 1):
                if 0 <= field + delta <= limit:
                    moved = value + delta * step
                    yield (moved, color1) if which == 0 else (color0, moved)


def _search_color(colors, starts, transparent: bool) -> tuple[int, int]:
    """Endpoints with the least weighted error: fit by least squares, then step to better neighbours."""
    def valid(ends):
        return ends[0] <= ends[1] if transparent else ends[0] > ends[1]

    best: tuple[int, tuple[int, int]] | None = None
    for start in starts:
        ends, error = start, _color_error(colors, *start, transparent)
        for _ in range(64):
            changed = False
            if (fitted := _least_squares_endpoints(colors, *ends, transparent)) is not None:
                fitted = (fitted[1], fitted[0]) if not valid(fitted) else fitted
                if valid(fitted) and (e := _color_error(colors, *fitted, transparent)) < error:
                    ends, error, changed = fitted, e, True
            step = min(((e, i, n) for i, n in enumerate(_color_neighbours(*ends)) if valid(n)
                        and (e := _color_error(colors, *n, transparent)) < error), default=None)
            if step:
                ends, error, changed = step[2], step[0], True
            if not changed or error == 0:
                break
        if best is None or error < best[0]:
            best = error, ends
    return best[1]


def _encode_color_block(
    pixels: tuple[tuple[int, int, int, int], ...], *, allow_transparency: bool
) -> bytes:
    transparent = allow_transparency and any(pixel[3] < 128 for pixel in pixels)
    start = _baseline_endpoints(pixels, allow_transparency=allow_transparency)
    # What a pixel costs: 1 when only the colour counts (DXT1), else its squared alpha.
    weights: dict[tuple[int, int, int], int] = {}
    for red, green, blue, alpha in pixels:
        weight = (alpha >= 128) if allow_transparency else alpha * alpha
        if weight:
            weights[red, green, blue] = weights.get((red, green, blue), 0) + int(weight)
    colors = tuple((*rgb, weight) for rgb, weight in weights.items())
    color0, color1 = start
    if colors:
        low = tuple(min(rgb[c] for rgb in weights) for c in range(3))
        high = tuple(max(rgb[c] for rgb in weights) for c in range(3))
        corners = [_rgba_to_rgb565((*low, 255)), _rgba_to_rgb565((*high, 255))]
        corners = sorted(corners, reverse=not transparent)
        starts = [start] + ([tuple(corners)] if corners[0] != corners[1] or transparent else [])
        color0, color1 = _search_color(colors, starts, transparent)
    palette = _palette_rgb(color0, color1, transparent)
    candidates = palette[:3] if transparent else palette
    indices = 0
    nearest: dict[tuple[int, int, int], int] = {}
    for ordinal, (red, green, blue, alpha) in enumerate(pixels):
        if transparent and alpha < 128:
            selected = 3
        else:
            selected = nearest.get((red, green, blue))
            if selected is None:
                distances = [(red - r) ** 2 + (green - g) ** 2 + (blue - b) ** 2 for r, g, b in candidates]
                selected = nearest[red, green, blue] = distances.index(min(distances))
        indices |= selected << (ordinal * 2)
    return struct.pack("<HHI", color0, color1, indices)


@lru_cache(maxsize=1 << 12)
def _alpha_levels(a0: int, a1: int) -> tuple[int, ...]:
    return _alpha_palette(a0, a1)


_ALPHA_SIZES = (16, 8, 4, 2, 1)
_ALPHA_GRID = (0, 64, 128, 192, 255)


def _alpha_error(counts, a0: int, a1: int) -> int:
    levels = _alpha_levels(a0, a1)
    return sum(count * min((value - level) ** 2 for level in levels) for value, count in counts)


@lru_cache(maxsize=1 << 16)
def _search_alpha(counts) -> tuple[int, int]:
    """Alpha endpoints with the least squared error. Two or fewer alphas are exact at (max, min). Otherwise
    each start (the extremes in either interpolation mode, and a coarse grid of pairs) moves by steps of 16,
    8, 4, 2 and 1 to its best neighbour, until none is better: the grid finds the endpoints inside the range
    that the extremes cannot."""
    low, high = counts[0][0], counts[-1][0]
    if len(counts) <= 2:
        return high, low
    known: dict[tuple[int, int], int] = {}

    def error(ends: tuple[int, int]) -> int:
        if ends not in known:
            known[ends] = _alpha_error(counts, *ends)
        return known[ends]

    best: tuple[int, tuple[int, int]] | None = None
    for ends in ((high, low), (low, high), *((x, y) for x in _ALPHA_GRID for y in _ALPHA_GRID)):
        for size in _ALPHA_SIZES:
            while True:
                moves = ((-size, 0), (size, 0), (0, -size), (0, size), (-size, -size), (size, size), (-size, size), (size, -size))
                step = min(((error(n), i, n) for i, (d0, d1) in enumerate(moves)
                            if 0 <= (n := (ends[0] + d0, ends[1] + d1))[0] <= 255 and 0 <= n[1] <= 255
                            and error(n) < error(ends)), default=None)
                if step is None:
                    break
                ends = step[2]
        if best is None or error(ends) < best[0]:
            best = error(ends), ends
    return best[1]


def _encode_alpha_block(
    pixels: tuple[tuple[int, int, int, int], ...]
) -> bytes:
    alphas = [pixel[3] for pixel in pixels]
    a0, a1 = _search_alpha(tuple(sorted((value, alphas.count(value)) for value in set(alphas))))
    palette = _alpha_palette(a0, a1)
    indices = 0
    nearest: dict[int, int] = {}
    for ordinal, alpha in enumerate(alphas):
        selected = nearest.get(alpha)
        if selected is None:  # the nearest level, the lowest index on a tie
            distances = [abs(alpha - value) for value in palette]
            selected = nearest[alpha] = distances.index(min(distances))
        indices |= selected << (ordinal * 3)
    return bytes((a0, a1)) + indices.to_bytes(6, "little")


# Cache repeated DXT blocks.
@lru_cache(maxsize=4096)
def _dxt1_block(block: bytes) -> bytes:
    return _encode_color_block(_pixels(block), allow_transparency=True)


@lru_cache(maxsize=4096)
def _dxt5_block(block: bytes) -> bytes:
    pixels = _pixels(block)
    return _encode_alpha_block(pixels) + _encode_color_block(pixels, allow_transparency=False)


def x360_g1t(data: bytes) -> G1tFile:
    """Convert an Xbox 360 G1T to the PC layout."""
    if data[:4] != b"G1TG":
        raise TextureCodecError("not an Xbox 360 G1T")
    _, identity, size, table, count, _, field_24, field_28 = struct.unpack_from(">4s4sIIIIII", data)
    offsets = struct.unpack_from(f">{count}I", data, table)
    starts = [table + o for o in offsets] + [size]
    out = bytearray(G1T_HEADER.pack(G1T_MAGIC, identity, 0, table, count, 0, field_24, field_28))
    out += data[len(out):table] + struct.pack(f"<{count}I", *offsets) + data[table + 4 * count:starts[0]]
    for start, end in zip(starts, starts[1:]):
        header = bytearray(data[start:start + 8])
        for k in (0, 2, 6):
            header[k] = (header[k] >> 4) | (header[k] & 15) << 4
        if header[1] not in (PIXEL_FORMAT_DXT1, PIXEL_FORMAT_DXT5):
            raise TextureCodecError(f"unsupported Xbox 360 texture format 0x{header[1]:02x}")
        payload = data[start + 8:end]
        swapped = bytearray(len(payload))
        swapped[0::2], swapped[1::2] = payload[1::2], payload[0::2]
        out += header + swapped
    struct.pack_into("<I", out, 8, len(out))
    return parse_g1t(bytes(out))
