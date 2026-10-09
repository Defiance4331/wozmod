"""Native donor plate cleanup."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median

from PIL import Image, ImageFilter

@dataclass(frozen=True)
class PlateSpec:
    """One destination and its native donor plate."""

    reference: tuple[int, int, int]
    role: str
    native_size: tuple[int, int]
    cleanup_reference: tuple[int, int, int] = (9, 5, 0)
    left_bevel: int = 4
    restore_fade: bool = True

X360_PLATES = {
    (4060, 23): PlateSpec((1, 23, 0), "4060.23", (256, 32)),
    (4060, 24): PlateSpec((1, 24, 0), "4060.24", (256, 32)),
    (4060, 25): PlateSpec((1, 25, 0), "4060.25", (256, 32)),
    (4060, 26): PlateSpec((1, 26, 0), "4060.26", (256, 32)),
    (4060, 36): PlateSpec((1, 36, 0), "4060.36", (256, 32), (1, 39, 0), 2, False),
    (4060, 37): PlateSpec((1, 37, 0), "4060.37", (256, 32), (1, 39, 0), 2, False),
    (4060, 38): PlateSpec((1, 38, 0), "4060.38", (256, 32), (1, 39, 0), 2, False),
    (4060, 39): PlateSpec((1, 39, 0), "4060.39", (256, 32), (1, 39, 0), 2, False),
    (4060, 40): PlateSpec((1, 40, 0), "4060.40", (512, 32), (1, 39, 0), 2, False),
    (4060, 41): PlateSpec((1, 40, 0), "4060.41", (512, 32), (1, 39, 0), 2, False),
    (4060, 42): PlateSpec((1, 42, 0), "4060.42", (256, 32), (1, 39, 0), 2, False),
    (4060, 43): PlateSpec((1, 43, 0), "4060.43", (256, 32), (1, 39, 0), 2, False),
    (4064, 22): PlateSpec((4, 43, 0), "4064.22", (256, 32), (1, 39, 0), 2, False),
    (4064, 23): PlateSpec((4, 44, 0), "4064.23", (256, 32)),
    (4064, 24): PlateSpec((4, 45, 0), "4064.24", (128, 32)),
    (4074, 3): PlateSpec((9, 3, 0), "4074.3", (256, 32)),
    (4074, 4): PlateSpec((9, 4, 0), "4074.4", (256, 32)),
    (4074, 5): PlateSpec((9, 5, 0), "4074.5", (256, 32)),
    (4074, 6): PlateSpec((9, 6, 0), "4074.6", (256, 32)),
    (4077, 26): PlateSpec((18, 11, 0), "4077.26", (256, 32)),
    (4077, 27): PlateSpec((12, 18, 0), "4077.27", (256, 32)),
    (4077, 28): PlateSpec((12, 19, 0), "4077.28", (256, 32)),
    (4081, 39): PlateSpec((9, 6, 0), "4081.39", (256, 32)),
    (4081, 40): PlateSpec((28, 6, 0), "4081.40", (256, 32)),
    (4081, 41): PlateSpec((28, 7, 0), "4081.41", (256, 32)),
    (4085, 9): PlateSpec((18, 10, 0), "4085.9", (256, 32)),
    (4085, 10): PlateSpec((18, 11, 0), "4085.10", (256, 32)),
    (4085, 11): PlateSpec((18, 12, 0), "4085.11", (256, 32)),
    (4094, 3): PlateSpec((18, 11, 0), "4094.3", (256, 32)),
    (4098, 6): PlateSpec((28, 6, 0), "4098.6", (256, 32)),
    (4098, 7): PlateSpec((28, 7, 0), "4098.7", (256, 32)),
}
X360_PLATE_ENTRIES = frozenset(
    reference[0] for spec in X360_PLATES.values()
    for reference in (spec.reference, spec.cleanup_reference)
)

def _row_colours(image: Image.Image, bounds: tuple,
                 avoid: Image.Image | None = None) -> list[tuple[int, int, int, int]]:
    """Sample the unlettered plate body on each native row."""
    pixels = image.load()
    left, _, right, _ = bounds

    stop = max(left + 3, right - image.height)
    colours = []
    for y in range(image.height):
        candidates = [x for x in range(left + 2, stop)
                      if avoid is None or not avoid.getpixel((x, y))]
        neutral = [pixels[x, y] for x in candidates
                   if pixels[x, y][3] >= 250
                   and min(pixels[x, y][:3]) >= 135
                   and max(pixels[x, y][:3]) - min(pixels[x, y][:3]) <= 12]
        if not neutral:
            neutral = [pixels[x, y] for x in range(left + 2, right - 2)
                       if pixels[x, y][3] >= 250
                       and min(pixels[x, y][:3]) >= 135
                       and max(pixels[x, y][:3]) - min(pixels[x, y][:3]) <= 12]
        if not neutral:
            colours.append((0, 0, 0, 0))
            continue
        counts = Counter(neutral)
        midpoint = median(sum(pixel[:3]) for pixel in neutral)
        colour = min(counts, key=lambda pixel: (-counts[pixel],
                                               abs(sum(pixel[:3]) - midpoint), pixel))
        colours.append(colour)
    return colours

def clean_substrate(source: Image.Image, *, background_reference: Image.Image | None = None,
                    background_column: int = 96, left_bevel: int = 2, restore_fade: bool = False,
                    canvas_size: tuple[int, int] | None = None,
                    minimum_right: int | None = None) -> tuple[Image.Image, Image.Image, dict]:
    """Remove baked lettering and extend the clean plate without scaling."""
    image = source.convert("RGBA")
    bounds = image.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    if bounds is None:
        raise ValueError("X360 label plate has no visible native art")
    target = canvas_size or image.size
    if target[0] < image.width or target[1] != image.height:
        raise ValueError("X360 plate canvas must retain native width and height")
    colours = _row_colours(image, bounds)
    reference = None
    reference_right = 0
    if background_reference is not None:
        reference = background_reference.convert("RGBA")
        if reference.height != image.height or not 0 <= background_column < reference.width:
            raise ValueError("X360 cleanup reference must retain native height and clean column")
        reference_bounds = reference.getchannel("A").point(
            lambda value: 255 if value > 16 else 0,
        ).getbbox()
        if reference_bounds is None:
            raise ValueError("X360 cleanup reference has no visible native art")
        reference_right = reference_bounds[2]
        colours = [reference.getpixel((background_column, y)) for y in range(image.height)]
    pixels = image.load()
    seed = Image.new("L", image.size)
    ink = seed.load()
    row_edges = []
    for y in range(image.height):
        end = max((x + 1 for x in range(image.width) if pixels[x, y][3] > 16), default=0)
        row_edges.append(end)
        body = colours[y]
        for x in range(bounds[0] + 2, max(bounds[0] + 2, end - 2)):
            pixel = pixels[x, y]
            if pixel[3] <= 16 or not body[3]:
                continue
            if (min(pixel[:3]) < 120 or max(pixel[:3]) - min(pixel[:3]) > 20
                    or min(pixel[:3]) < min(body[:3]) - 16):
                ink[x, y] = 255

    near = seed.filter(ImageFilter.MaxFilter(5))
    mask = Image.new("L", image.size)
    erased = mask.load()
    for y0 in range(0, image.height, 4):
        for x0 in range(0, image.width, 4):
            if near.crop((x0, y0, min(x0 + 4, image.width), min(y0 + 4, image.height))).getbbox():
                for y in range(y0, min(y0 + 4, image.height)):
                    for x in range(x0, min(x0 + 4, image.width)):

                        pixel = pixels[x, y]

                        foreground = (min(pixel[:3]) < 120
                                      or max(pixel[:3]) - min(pixel[:3]) > 20)
                        if ((bounds[0] + left_bevel <= x or ink[x, y])
                                and bounds[0] + 2 <= x < row_edges[y] - 2
                                and pixel[3] > 16 and colours[y][3]
                                and (reference is not None or pixel[3] >= 250
                                     or foreground or ink[x, y])):
                            erased[x, y] = 255
    clean = image.copy()

    if reference is None:
        colours = _row_colours(image, bounds, mask)
    cleaned = clean.load()
    changed = 0
    for y in range(image.height):
        for x in range(image.width):
            if erased[x, y]:
                replacement = (*colours[y][:3], pixels[x, y][3])
                if reference is not None:
                    reference_x = reference_right - (bounds[2] - x)
                    if (restore_fade and x >= bounds[2] - image.height
                            and 0 <= reference_x < reference.width):
                        replacement = reference.getpixel((reference_x, y))
                    else:
                        replacement = (*colours[y][:3], pixels[x, y][3])
                    if x < bounds[0] + left_bevel:

                        nearby = [pixels[x, row] for row in range(max(0, y - 4),
                                                                 min(image.height, y + 5))
                                  if not ink[x, row] and pixels[x, row][3] >= 250
                                  and max(pixels[x, row][:3]) - min(pixels[x, row][:3]) <= 12]
                        if nearby:
                            replacement = tuple(int(median(p[channel] for p in nearby))
                                                for channel in range(4))
                changed += pixels[x, y] != replacement
                cleaned[x, y] = replacement
    old_right = bounds[2]
    right = max(old_right, minimum_right or old_right)
    if right > target[0]:
        raise ValueError(f"full-width X360 plate needs a {right}-wide canvas")
    output = Image.new("RGBA", target)
    output.paste(clean, (0, 0))
    extension = right - old_right
    tail_start = max(bounds[0] + 2, old_right - image.height)
    if extension:

        tail = clean.crop((tail_start, 0, image.width, image.height))
        output.paste((0, 0, 0, 0), (tail_start, 0, target[0], image.height))
        stripe = Image.new("RGBA", (1, image.height))
        stripe.putdata(colours)
        output.paste(stripe.resize((extension, image.height), Image.Resampling.NEAREST),
                     (tail_start, 0))
        output.paste(tail, (tail_start + extension, 0))
    metadata = dict(method="native column and end reconstruction in letter/DXT-fringe blocks"
                           if reference is not None else
                           "native row-colour reconstruction in letter/DXT-fringe blocks",
                    source_size=list(image.size), canvas_size=list(target),
                    source_art_box=list(bounds), erase_box=mask.getbbox(),
                    erase_pixels=sum(bool(value) for value in mask.get_flattened_data()),
                    changed_pixels=changed, old_right=old_right, right=right,
                    extension=extension, tail_start=tail_start,
                    background_column=background_column if reference is not None else None,
                    left_bevel=left_bevel, restore_fade=restore_fade,
                    row_colours=[list(colour) for colour in colours],
                    preserved="all source pixels outside erase mask; relocated native end")
    return output, mask, metadata
