from __future__ import annotations

import random
import struct

import pytest

from wozmod.formats import textures
from wozmod.formats.textures import TextureCodecError, parse_g1t, parse_texture_package, x360_g1t


def _g1t(*textures: tuple[int, int, int, bytes], mipmaps: int = 1) -> bytes:
    spans = [struct.pack("<BBHI", mipmaps << 4, fmt, wp | hp << 4, 0x11111) + payload
             for fmt, wp, hp, payload in textures]
    position = 4 * len(spans)
    offsets = []
    for span in spans:
        offsets.append(position)
        position += len(span)
    return (struct.pack("<4s4s6I", b"GT1G", b"test", 32 + position, 32, len(spans), 0, 0, 0)
            + struct.pack(f"<{len(offsets)}I", *offsets) + b"".join(spans))


def _package(*items: bytes) -> bytes:
    head = bytearray(struct.pack("<4I", 0x33B59, len(items), 16, 0))
    position = (16 + len(items) * 8 + 15) // 16 * 16
    body = bytearray()
    for item in items:
        head += struct.pack("<2I", position // 16, len(item))
        padded = item + bytes(-len(item) % 16)
        body += padded
        position += len(padded)
    return bytes(head + bytes(-len(head) % 16) + body)


def test_bgra_decode_does_not_leave_red_and_blue_swapped() -> None:
    source = _g1t((1, 1, 0, bytes((3, 2, 1, 4, 7, 6, 5, 8))))
    g1t = parse_g1t(source)
    assert g1t.textures[0].decode_rgba() == bytes((1, 2, 3, 4, 5, 6, 7, 8))
    assert g1t.encode() == source
    assert g1t.textures[0].pixel_format_name == "bgra8"


def test_bgra_replacement_does_not_write_rgba_as_bgra() -> None:
    source = _g1t((1, 0, 0, bytes(4)))
    changed = parse_g1t(source).with_texture_rgba(0, bytes((17, 34, 51, 68)))
    assert changed.encode() == source[:-4] + bytes((51, 34, 17, 68))


def test_unchanged_dxt_pixels_do_not_recompress_original_endpoints() -> None:
    payload = struct.pack("<HHI", 0xFFFF, 0x1234, 0)
    g1t = parse_g1t(_g1t((6, 2, 2, payload)))
    assert g1t.with_texture_rgba(0, bytes((255, 255, 255, 255)) * 16).encode() == g1t.encode()


def test_dxt1_palette_order_does_not_drop_transparency_or_interpolation() -> None:
    indices = sum((i % 4) << (2 * i) for i in range(16))
    cases = (
        (0xFFFF, 0, [(255, 255, 255, 255), (0, 0, 0, 255), (170, 170, 170, 255), (85, 85, 85, 255)],
         {"dxt1-four-color": 1}),
        (0, 0xFFFF, [(0, 0, 0, 255), (255, 255, 255, 255), (127, 127, 127, 255), (0, 0, 0, 0)],
         {"dxt1-three-color-transparent": 1}),
    )
    for first, second, palette, modes in cases:
        texture = parse_g1t(_g1t((6, 2, 2, struct.pack("<HHI", first, second, indices)))).textures[0]
        assert texture.decode_rgba() == b"".join(map(bytes, palette)) * 4
        assert texture.palette_modes() == modes


def test_dxt5_alpha_order_does_not_select_the_wrong_interpolation_branch() -> None:
    indices = sum((i % 8) << (3 * i) for i in range(16))
    cases = (
        (255, 0, [255, 0, 218, 182, 145, 109, 72, 36], {"dxt5-alpha-eight": 1}),
        (0, 255, [0, 255, 51, 102, 153, 204, 0, 255], {"dxt5-alpha-six-plus-extremes": 1}),
    )
    for first, second, alphas, modes in cases:
        payload = bytes((first, second)) + indices.to_bytes(6, "little") + struct.pack("<HHI", 0xFFFF, 0, 0)
        texture = parse_g1t(_g1t((8, 2, 2, payload))).textures[0]
        assert texture.decode_rgba() == b"".join(bytes((255, 255, 255, alpha)) for alpha in alphas) * 2
        assert texture.palette_modes() == modes


def test_dxt_encode_does_not_quantize_red_to_the_wrong_rgb565_endpoint() -> None:
    for fmt, blank, expected in (
        (6, bytes(8), struct.pack("<HHI", 0xF801, 0xF800, 0x55555555)),
        (8, bytes(16), bytes((255, 255)) + bytes(6) + struct.pack("<HHI", 0xF801, 0xF800, 0x55555555)),
    ):
        texture = parse_g1t(_g1t((fmt, 2, 2, blank))).textures[0]
        assert texture.encode_rgba(bytes((255, 0, 0, 255)) * 16) == expected


def _blocks_under_test() -> list[tuple[str, tuple[tuple[int, int, int, int], ...]]]:
    rng = random.Random(20261012)
    black, blue, red = (0, 0, 0, 255), (0, 0, 255, 255), (255, 0, 0, 255)
    rows = [("two exact colours", tuple([black, blue] * 8)), ("red, blue, black", tuple([blue, red] + [black] * 14))]
    for number in range(60):  # a stroke edge: two colours, a ramp between them, antialiased alpha
        low, high = (rng.randrange(256) for _ in range(3)), (rng.randrange(256) for _ in range(3))
        low, high = tuple(low), tuple(high)
        rows.append((f"edge{number}", tuple(
            (*(low[k] + (high[k] - low[k]) * t // 15 for k in range(3)), rng.choice((0, 255, 255, rng.randrange(256))))
            for t in rng.sample(range(16), 16))))
    for number in range(30):
        rows.append((f"noise{number}", tuple((rng.randrange(256), rng.randrange(256), rng.randrange(256), 255) for _ in range(16))))
    return rows


def _visible_cost(pixels, ends) -> int:
    return textures._visible_error(pixels, ends)


def test_dxt5_colour_search_is_never_worse_than_the_quick_endpoints_and_beats_them() -> None:
    # Bug: the search keeps a candidate that is worse than its start (error is not compared), or does not
    # search at all and returns the darkest/brightest-pixel endpoints that the old encoder used.
    worse, new_total, old_total = [], 0, 0
    for name, pixels in _blocks_under_test():
        block = textures._encode_color_block(pixels, allow_transparency=False)
        ends = struct.unpack_from("<HH", block)
        old = textures._baseline_endpoints(pixels, allow_transparency=False)
        new_total, old_total = new_total + _visible_cost(pixels, ends), old_total + _visible_cost(pixels, old)
        if _visible_cost(pixels, ends) > _visible_cost(pixels, old):
            worse.append(name)
    assert worse == []
    assert new_total < 0.8 * old_total


def test_dxt5_alpha_search_is_never_worse_than_the_extremes_and_beats_them() -> None:
    # Bug: alpha endpoints stay at (max, min) although another pair, or the six-level mode with its exact
    # 0 and 255, fits antialiased edges better; or a worse pair is kept.
    worse, new_total, old_total = [], 0, 0
    for name, pixels in _blocks_under_test():
        alphas = [p[3] for p in pixels]
        counts = tuple(sorted((v, alphas.count(v)) for v in set(alphas)))
        a0, a1 = struct.unpack_from("BB", textures._encode_alpha_block(pixels))
        new, old = textures._alpha_error(counts, a0, a1), textures._alpha_error(counts, max(alphas), min(alphas))
        new_total, old_total = new_total + new, old_total + old
        if new > old:
            worse.append(name)
    assert worse == []
    assert new_total < 0.8 * old_total


def _g1t_dxt5(width: int, height: int, payload: bytes):
    return parse_g1t(_g1t((8, width.bit_length() - 1, height.bit_length() - 1, payload))).textures[0]


def test_a_dxt_edit_leaves_every_block_it_did_not_touch_byte_exact() -> None:
    # Bug: the whole texture is decoded and encoded again, so blocks the edit never touched lose a
    # generation (each is re-quantised against a fresh endpoint search).
    rng = random.Random(7)
    payload = b"".join(rng.randbytes(16) for _ in range(16))  # 16x16 DXT5, arbitrary valid blocks
    texture = _g1t_dxt5(16, 16, payload)
    rgba = bytearray(texture.decode_rgba())
    for y in range(4, 8):  # one block, row 1 column 1, painted white
        rgba[(y * 16 + 4) * 4:(y * 16 + 8) * 4] = bytes((255, 255, 255, 255)) * 4

    out = texture.encode_rgba(bytes(rgba))

    blocks = lambda data: [data[i:i + 16] for i in range(0, 256, 16)]
    assert [i for i, (a, b) in enumerate(zip(blocks(payload), blocks(out), strict=True)) if a != b] == [5]
    assert _g1t_dxt5(16, 16, out).decode_rgba()[(4 * 16 + 4) * 4:(4 * 16 + 8) * 4] == bytes((255, 255, 255, 255)) * 4


def test_a_resized_dxt_texture_keeps_the_old_area_byte_exact() -> None:
    # Bug: enlarging a texture decodes the old art and encodes it again with the new blocks.
    rng = random.Random(8)
    payload = b"".join(rng.randbytes(16) for _ in range(4))  # 8x8
    source = _g1t((8, 3, 3, payload))
    g1t = parse_g1t(source)
    canvas = bytearray(16 * 16 * 4)
    old = g1t.textures[0].decode_rgba()
    for y in range(8):
        canvas[y * 16 * 4:y * 16 * 4 + 32] = old[y * 32:(y + 1) * 32]

    grown = g1t.with_texture_resized(0, 16, 16, bytes(canvas)).textures[0].payload

    assert [grown[i:i + 16] for i in (0, 16, 64, 80)] == [payload[i:i + 16] for i in (0, 16, 32, 48)]
    decoded = _g1t_dxt5(16, 16, grown).decode_rgba()
    assert all(decoded[(y * 16 + x) * 4 + 3] == 0 for y in range(16) for x in range(16) if x >= 8 or y >= 8)


def test_dxt5_endpoint_selection_does_not_flatten_glow_or_band_opaque_pixels() -> None:
    navy, glow = (16, 24, 66), (222, 209, 247)
    glow_block = [(*navy, 255), (*navy, 255), (*glow, 96), (*glow, 40)] * 4
    shadow_block = [(0, 0, 0, 5)] + [(120 + 8*i, 100 + 6*i, 70 + 4*i, 255) for i in range(15)]
    g1t = parse_g1t(_g1t((8, 2, 2, bytes(16))))
    for pixels in (glow_block, shadow_block):
        decoded = g1t.with_texture_rgba(0, b"".join(map(bytes, pixels))).textures[0].decode_rgba()
        shown = [round(pixel[3] / 255 * max(abs(a - b) for a, b in zip(pixel[:3], decoded[4*i:4*i+3], strict=True)))
                 for i, pixel in enumerate(pixels)]
        assert max(shown) <= 20, shown


def test_top_mip_replacement_does_not_discard_the_lower_mip_tail() -> None:
    tail = bytes((9, 8, 7, 6))
    g1t = parse_g1t(_g1t((1, 1, 1, bytes(16) + tail), mipmaps=2))
    changed = g1t.with_texture_rgba(0, bytes((1, 2, 3, 4)) * 4)
    assert changed.textures[0].payload == bytes((3, 2, 1, 4)) * 4 + tail


def test_g1t_resize_does_not_leave_later_texture_offsets_stale() -> None:
    later = bytes((3, 2, 1, 255))
    source = _g1t((1, 0, 0, bytes(4)), (1, 0, 0, later))
    changed = parse_g1t(source).with_texture_resized(0, 2, 1, bytes((1, 2, 3, 4)) * 2)
    assert changed.texture_offsets == (8, 24)
    assert [(t.offset, t.end, t.width, t.height) for t in changed.textures] == [(40, 56, 2, 1), (56, 68, 1, 1)]
    assert changed.textures[1].payload == later
    assert struct.unpack_from("<I", changed.encode(), 8)[0] == 68


def test_package_parser_does_not_drop_empty_items() -> None:
    g1t = _g1t((1, 0, 0, bytes((3, 2, 1, 255))))
    source = _package(g1t, b"")
    package = parse_texture_package(source)
    assert [(item.ordinal, item.identifier, item.offset, item.source, item.g1t is None) for item in package.items] == [
        (0, 2, 32, g1t, False), (1, 5, 80, b"", True)]
    assert package.encode() == source
    assert package.with_g1t(0, package.items[0].g1t).encode() == source


def test_package_resize_recomputes_every_later_record_offset() -> None:
    first = _g1t((1, 0, 0, bytes(4)))
    later = _g1t((1, 0, 0, bytes((3, 2, 1, 255))))
    package = parse_texture_package(_package(first, b"", later))
    resized = package.items[0].g1t.with_texture_resized(0, 4, 4, bytes((1, 2, 3, 4)) * 16)
    changed = package.with_g1t(0, resized)
    assert [(item.identifier, item.offset, len(item.source)) for item in changed.items] == [(3, 48, 108), (10, 160, 0), (10, 160, 48)]
    assert changed.items[2].source == later


def test_g1t_run_replacement_keeps_sector_alignment_instead_of_package_alignment() -> None:
    first = _g1t((1, 0, 0, bytes(4)))
    later = _g1t((1, 0, 0, bytes((3, 2, 1, 255))))
    source = first + bytes(2048 - len(first)) + later + bytes(2048 - len(later))
    run = parse_texture_package(source)
    changed_g1t = run.items[0].g1t.with_texture_resized(0, 32, 32, bytes((1, 2, 3, 4)) * 1024)
    changed = run.with_g1t(0, changed_g1t)
    expected_first = changed_g1t.encode()
    expected = expected_first + bytes(6144 - len(expected_first)) + source[2048:]
    assert changed.run and [item.offset for item in changed.items] == [0, 6144]
    assert changed.encode() == expected


def test_package_padding_rejection_does_not_accept_overlapping_payload_bytes() -> None:
    source = _package(_g1t((8, 2, 2, bytes(16))))
    with pytest.raises(TextureCodecError, match="padding is not zero"):
        parse_texture_package(source[:-1] + bytes((1,)))


def test_unknown_g1t_format_is_not_silently_treated_as_dxt() -> None:
    with pytest.raises(TextureCodecError):
        parse_g1t(_g1t((255, 2, 2, bytes(16))))


def test_x360_conversion_does_not_miss_word_swaps_or_header_nibble_swaps() -> None:
    red = struct.pack(">HHI", 0xF800, 0x001F, 0)
    blue = struct.pack(">HHI", 0x001F, 0xF800, 0)
    header = bytes((1, 6, 0x32, 0, 0x11, 0x11, 0x10, 0))
    source = (struct.pack(">4s4s6I", b"G1TG", b"test", 60, 32, 1, 2, 0, 0)
              + struct.pack(">I", 4) + header + red + blue)
    converted = x360_g1t(source)
    expected = _g1t((6, 3, 2, struct.pack("<HHI", 0xF800, 0x001F, 0) + struct.pack("<HHI", 0x001F, 0xF800, 0)))
    assert converted.encode() == expected
    assert converted.textures[0].decode_rgba() == (bytes((255, 0, 0, 255)) * 4 + bytes((0, 0, 255, 255)) * 4) * 4


def test_a_texture_resized_into_bgra8_keeps_every_pixel_and_says_bgra8() -> None:
    # Bug: the new pixel format is written into the payload but not the texture header (or the reverse),
    # so the game reads uncompressed pixels as DXT blocks, and every later texture offset moves wrongly.
    g1t = parse_g1t(_g1t((8, 2, 2, bytes(16)), (6, 2, 2, bytes(8))))
    rgba = bytes((i * 7) % 256 for i in range(8 * 8 * 4))

    grown = g1t.with_texture_resized(0, 8, 8, rgba, textures.PIXEL_FORMAT_BGRA8)

    first, second = grown.textures
    assert (first.pixel_format_name, first.width, first.height) == ("bgra8", 8, 8)
    assert first.decode_rgba() == rgba
    assert second.pixel_format_name == "dxt1" and second.payload == bytes(8)
    with pytest.raises(TextureCodecError):
        g1t.with_texture_resized(0, 8, 8, rgba, 0x7F)
