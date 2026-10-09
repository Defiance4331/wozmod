"""G1T containers, texture packages and DXT codecs."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import lru_cache

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
            output = bytearray(len(payload))
            for offset in range(0, len(payload), 4):
                blue, green, red, alpha = payload[offset : offset + 4]
                output[offset : offset + 4] = bytes((red, green, blue, alpha))
            return bytes(output)
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
        top = _encode_top(self.pixel_format, rgba, self.width, self.height)
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
        self, texture_index: int, width: int, height: int, rgba: bytes
    ) -> G1tFile:
        """Resize a single-mip texture and move later textures."""

        texture = self.textures[texture_index]
        if texture.mipmap_count != 1:
            raise TextureCodecError("only single-mip textures can be resized")
        if width & (width - 1) or height & (height - 1) or len(rgba) != width * height * 4:
            raise TextureCodecError("resized texture needs power-of-two RGBA pixels")
        header = G1T_TEXTURE_HEADER.pack(
            1 << 4,
            texture.pixel_format,
            (width.bit_length() - 1) | (height.bit_length() - 1) << 4,
            texture.flags,
        )
        span = header + _encode_top(texture.pixel_format, bytes(rgba), width, height)
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


def _encode_top(pixel_format: int, rgba: bytes, width: int, height: int) -> bytes:
    if pixel_format == PIXEL_FORMAT_BGRA8:
        encoded = bytearray(len(rgba))
        for offset in range(0, len(rgba), 4):
            red, green, blue, alpha = rgba[offset : offset + 4]
            encoded[offset : offset + 4] = bytes((blue, green, red, alpha))
        return bytes(encoded)
    if pixel_format == PIXEL_FORMAT_DXT1:
        return _encode_dxt1(rgba, width, height)
    if pixel_format == PIXEL_FORMAT_DXT5:
        return _encode_dxt5(rgba, width, height)
    raise AssertionError("unreachable pixel format")


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


# Cache repeated DXT blocks.
@lru_cache(maxsize=4096)
def _decode_block(block: bytes, alpha: bool) -> tuple[bytes, bytes, bytes, bytes]:
    """Decode one DXT block."""

    alpha_values = (255,) * 8
    alpha_indices = 0
    position = 0
    if alpha:
        alpha_values = _alpha_palette(block[0], block[1])
        alpha_indices = int.from_bytes(block[2:8], "little")
        position = 8
    color0, color1, color_indices = struct.unpack_from("<HHI", block, position)
    colors = _color_palette(color0, color1, force_four=alpha)
    rows = []
    for local_y in range(4):
        row = bytearray()
        for local_x in range(4):
            index = local_y * 4 + local_x
            red, green, blue, decoded_alpha = colors[(color_indices >> (index * 2)) & 0x03]
            if alpha:
                decoded_alpha = alpha_values[(alpha_indices >> (index * 3)) & 0x07]
            row += bytes((red, green, blue, decoded_alpha))
        rows.append(bytes(row))
    return tuple(rows)  # type: ignore[return-value]


def _decode_dxt(payload: bytes, width: int, height: int, *, alpha: bool) -> bytes:
    block_bytes = 16 if alpha else 8
    blocks_wide = max(1, (width + 3) // 4)
    blocks_high = max(1, (height + 3) // 4)
    if len(payload) != blocks_wide * blocks_high * block_bytes:
        raise TextureCodecError("DXT payload size does not match its geometry")
    output = bytearray(width * height * 4)
    position = 0
    for block_y in range(blocks_high):
        for block_x in range(blocks_wide):
            rows = _decode_block(payload[position : position + block_bytes], alpha)
            position += block_bytes
            x = block_x * 4
            span = 4 * (min(width, x + 4) - x)
            for local_y, row in enumerate(rows):
                y = block_y * 4 + local_y
                if y < height:
                    offset = (y * width + x) * 4
                    output[offset : offset + span] = row[:span]
    return bytes(output)


def _block_pixels(rgba: bytes, width: int, height: int, block_x: int, block_y: int) -> tuple[tuple[int, int, int, int], ...]:
    pixels = []
    for local_y in range(4):
        y = min(height - 1, block_y * 4 + local_y)
        for local_x in range(4):
            x = min(width - 1, block_x * 4 + local_x)
            offset = (y * width + x) * 4
            pixels.append(tuple(rgba[offset : offset + 4]))
    return tuple(pixels)  # type: ignore[return-value]


def _color_endpoints(
    pixels: tuple[tuple[int, int, int, int], ...], *, transparent: bool, visible: int = 128
) -> tuple[int, int]:
    opaque = tuple(pixel for pixel in pixels if pixel[3] >= visible) or pixels
    low = min(opaque, key=lambda pixel: (sum(pixel[:3]), pixel[:3]))
    high = max(opaque, key=lambda pixel: (sum(pixel[:3]), pixel[:3]))
    low565 = _rgba_to_rgb565(low)
    high565 = _rgba_to_rgb565(high)
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


def _visible_error(
    pixels: tuple[tuple[int, int, int, int], ...], endpoints: tuple[int, int]
) -> int:
    """Sum squared color error weighted by squared alpha."""
    palette = _color_palette(*endpoints, force_four=True)
    return sum(
        alpha * alpha * min((red - r) ** 2 + (green - g) ** 2 + (blue - b) ** 2 for r, g, b, _a in palette)
        for red, green, blue, alpha in pixels
    )


@lru_cache(maxsize=4096)
def _encode_color_block(
    pixels: tuple[tuple[int, int, int, int], ...], *, allow_transparency: bool
) -> bytes:
    transparent = allow_transparency and any(pixel[3] < 128 for pixel in pixels)
    color0, color1 = _color_endpoints(pixels, transparent=transparent)
    if not allow_transparency:  # DXT5 endpoint candidates include faint pixels.
        lit = _color_endpoints(pixels, transparent=False, visible=1)
        if lit != (color0, color1) and _visible_error(pixels, lit) < _visible_error(pixels, (color0, color1)):
            color0, color1 = lit
    palette = _color_palette(color0, color1, force_four=not allow_transparency)
    candidates = palette[:3] if transparent else palette
    indices = 0
    for ordinal, (red, green, blue, alpha) in enumerate(pixels):
        if transparent and alpha < 128:
            selected = 3
        else:  # the nearest colour, the lowest index on a tie
            distances = [
                (red - r) ** 2 + (green - g) ** 2 + (blue - b) ** 2
                for r, g, b, _a in candidates
            ]
            selected = distances.index(min(distances))
        indices |= selected << (ordinal * 2)
    return struct.pack("<HHI", color0, color1, indices)


@lru_cache(maxsize=4096)
def _encode_alpha_block(
    pixels: tuple[tuple[int, int, int, int], ...]
) -> bytes:
    alphas = tuple(pixel[3] for pixel in pixels)
    alpha0 = max(alphas)
    alpha1 = min(alphas)
    palette = _alpha_palette(alpha0, alpha1)
    indices = 0
    for ordinal, alpha in enumerate(alphas):
        distances = [abs(alpha - value) for value in palette]
        indices |= distances.index(min(distances)) << (ordinal * 3)
    return bytes((alpha0, alpha1)) + indices.to_bytes(6, "little")


def _encode_dxt1(rgba: bytes, width: int, height: int) -> bytes:
    output = bytearray()
    for block_y in range(max(1, (height + 3) // 4)):
        for block_x in range(max(1, (width + 3) // 4)):
            output.extend(
                _encode_color_block(
                    _block_pixels(rgba, width, height, block_x, block_y),
                    allow_transparency=True,
                )
            )
    return bytes(output)


def _encode_dxt5(rgba: bytes, width: int, height: int) -> bytes:
    output = bytearray()
    for block_y in range(max(1, (height + 3) // 4)):
        for block_x in range(max(1, (width + 3) // 4)):
            pixels = _block_pixels(rgba, width, height, block_x, block_y)
            output.extend(_encode_alpha_block(pixels))
            output.extend(_encode_color_block(pixels, allow_transparency=False))
    return bytes(output)


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
