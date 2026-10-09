"""Native plate cleanup preserves gradients, bevels and alpha."""

from PIL import Image

from wozmod.fonts import menu_plates as plates


def native_plate() -> Image.Image:
    """A small faithful striped body, native bevel and diagonal decorative end."""
    image = Image.new("RGBA", (40, 16))
    for y in range(image.height):
        body = 210 - 3 * y
        end = 24 + y // 2
        for x in range(end):
            colour = (body, body, body, 255)
            if x < 2 or x >= end - 2:
                colour = (body + 20, body + 20, body + 20, 255)
            image.putpixel((x, y), colour)
    return image


def test_erasing_console_letter_core_cannot_leave_its_neutral_dxt_fringe():
    original = native_plate()
    # The native plate can fade beneath the console letters at its end.
    original.putpixel((12, 8), (*original.getpixel((12, 8))[:3], 216))
    baked = original.copy()
    for y in range(4, 11):
        body = original.getpixel((10, y))[0]
        for x in range(7, 15):
            colour = (body - 12,) * 3 + (255,)  # pale, nearly neutral DXT fringe
            if 9 <= x < 13 and 6 <= y < 9:
                colour = (16, 44, 99, 255)
            baked.putpixel((x, y), (*colour[:3], original.getpixel((x, y))[3]))
    before = baked.tobytes()
    clean, mask, metadata = plates.clean_substrate(baked)
    # Matching the whole unlettered plate proves both complete halo removal
    # and retention of row gradients, transparent pixels and native bevels.
    assert clean.tobytes() == original.tobytes()
    assert baked.tobytes() == before


def test_widening_pc_label_plate_cannot_rescale_or_flatten_its_native_right_end():
    original = native_plate()
    # This faint native end pixel lies beyond the normal visible-art boundary.
    # Moving only the opaque end drops it or leaves it at the old position.
    original.putpixel((32, 15), (170, 170, 170, 8))
    clean, mask, metadata = plates.clean_substrate(
        original, canvas_size=(64, 16), minimum_right=48,
    )
    start, added = 15, 17
    # The exact decoded slant, bevel and alpha profile moves as one native
    # block; stretching or repainting it fails even when the box still fits.
    expected = Image.new("RGBA", (64, 16))
    expected.paste(original.crop((0, 0, start, 16)), (0, 0))
    for y in range(16):
        value = 210 - 3 * y
        for x in range(start, start + added):
            expected.putpixel((x, y), (value, value, value, 255))
    expected.paste(original.crop((start, 0, 40, 16)), (start + added, 0))
    assert clean.tobytes() == expected.tobytes()


def test_console_letters_over_a_fading_end_cannot_survive_as_alpha_ghosts():
    def fading_plate(extra_width: int = 0) -> Image.Image:
        image = Image.new("RGBA", (40 + extra_width, 16))
        for y in range(16):
            value = 210 - 3 * y
            for x in range(32 + extra_width):
                alpha = 255 if x < 24 + extra_width else (32 + extra_width - x) * 30
                image.putpixel((x, y), (value, value, value, alpha))
        return image

    original = fading_plate()
    # Console text is more opaque than the plate underneath. Colour-only
    # erasure leaves a readable silhouette despite removing every blue core.
    baked = original.copy()
    for y in range(5, 11):
        for x in range(23, 28):
            baked.putpixel((x, y), (16, 44, 99, 255))
    native_reference = fading_plate(8)
    clean, mask, metadata = plates.clean_substrate(
        baked, background_reference=native_reference, background_column=12,
        restore_fade=True,
    )
    assert clean.tobytes() == original.tobytes()
