"""The lettering faces' pixel loops, now in the native module, against the answers of the Python loops they replaced.

Each expected value was made by the replaced Python loop on the same synthetic image (built here from a fixed seed),
before it was deleted: a stroke list as is, an image as the sha256 of its RGBA bytes.
"""

from __future__ import annotations

import hashlib
import random

import pytest
from PIL import Image

from wozmod.art import build, faces, imaging

# colours the fills tell apart: yellow fill, an orange edge, a saturated red, faint and clear pixels, a dark outline
_PALETTE = [(230, 200, 40, 255), (200, 150, 30, 140), (180, 40, 30, 220), (90, 90, 90, 40), (0, 0, 0, 0),
            (250, 250, 250, 210), (20, 20, 30, 255), (160, 160, 150, 201), (150, 120, 20, 200)]


def _mottled(seed: int, width: int, height: int, clear: float = 0.3) -> Image.Image:
    """Patches of palette colours (strokes that touch only at corners, edge-touching strokes, holes of no alpha)."""
    rng, image = random.Random(seed), Image.new("RGBA", (width, height))
    for y in range(height):
        for x in range(width):
            if rng.random() < clear:
                image.putpixel((x, y), (rng.randrange(256), rng.randrange(256), rng.randrange(256), 0))
            else:
                image.putpixel((x, y), rng.choice(_PALETTE))
    return image


def _sha(image: Image.Image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()[:16]


def _digest(value) -> str:
    return hashlib.sha256(repr(value).encode("ascii")).hexdigest()[:16]


def _banded() -> Image.Image:
    """Rows of ink, blank rows, a row of alpha 16 (not ink) and one of 17 (ink)."""
    image = _mottled(9, 6, 12, clear=0)
    for y, alpha in ((0, 0), (3, 0), (4, 0), (7, 16), (8, 0), (10, 17)):
        for x in range(image.width):
            image.putpixel((x, y), (*image.getpixel((x, y))[:3], alpha))
    return image


def _letters() -> Image.Image:
    """A row of yellow letters with dark outlines, two of them sharing an outline column (no gap between)."""
    row = Image.new("RGBA", (64, 16))
    for x0, x1, y0 in ((2, 11, 3), (13, 22, 3), (22, 31, 5), (38, 47, 3), (49, 58, 4)):
        row.paste((20, 20, 30, 255), (x0, y0, x1, 14))
        row.paste((230, 200, 40, 255), (x0 + 1, y0 + 1, x1 - 1, 13))
    row.putpixel((31, 9), (20, 20, 30, 120))  # a soft outline pixel only the third letter reaches
    return row


# (seed, size, fill, least): (how many strokes, digest of the strokes, each sorted, in their order)
STROKES = {(1, (16, 9), "yellow", 3): (5, "fe8dd56ff3a5c85e"), (2, (16, 9), "faint", 1): (19, "09489c2dff7a6c6f"),
           (3, (16, 9), "saturated", 2): (12, "9f21e35b4e82c833"), (4, (12, 12), "opaque", 1): (14, "53c81f7ec0516f28"),
           (5, (16, 9), "white", 1): (9, "6401c5c63d708500")}


@pytest.mark.parametrize("case", STROKES)
def test_strokes_are_the_old_loops_components_in_left_to_right_order(case) -> None:
    # Bug: a stroke joins pixels that touch only at a corner, keeps fewer than least pixels, tests a pixel by a
    # colour it does not have, or comes out of left-to-right order, so a glyph takes its neighbour's stroke. (The
    # old loop broke a tie between two strokes that start in one column in set order; now the top one is first.)
    seed, size, fill, least = case
    found = [sorted(stroke) for stroke in faces.strokes(_mottled(seed, *size), faces.FILLS[fill], least)]
    assert (len(found), _digest(found)) == STROKES[case]


# (seed, size, fill of the strokes): (how many cuts, digest of each cut's box and image sha256)
CUTS = {(10, (18, 10), "yellow"): (15, "a65bd4ca4536d0c8"), (11, (18, 10), "faint"): (29, "29179560a45c5be4"),
        (12, (14, 14), "solid"): (23, "d507d0065caeace4")}


@pytest.mark.parametrize("case", CUTS)
def test_each_fill_gets_the_pixels_the_old_loop_gave_it(case) -> None:
    # Bug: a pixel two fills reach in the same number of steps goes to the later fill, the search crosses a pixel
    # of no alpha or wraps at the image's edge, or a fill's own clear pixel loses its colour, so a glyph's outline
    # changes.
    seed, size, fill = case
    row = _mottled(seed, *size, clear=0.25)
    cuts = [(x0, y0, x1, y1, _sha(image))
            for x0, y0, x1, y1, image in faces.nearest(row, faces.strokes(row, faces.FILLS[fill], 1))]
    assert (len(cuts), _digest(cuts)) == CUTS[case]


# (seed, size, share of clear pixels, radius, strength): sha256 of the image unglowed
UNGLOWED = {(6, (20, 14), 0.3, 1.5, 1.2): "cd11ee2fbb80da09", (7, (20, 14), 0.3, 2.0, 6.0): "3d8201d7540e0068",
            (8, (24, 16), 0.9, 1.0, 2.0): "f3b9958a3e99be72"}


@pytest.mark.parametrize("case", UNGLOWED)
def test_unglowed_takes_out_the_glow_as_the_old_loop_did(case) -> None:
    # Bug: the alpha rounds half up instead of to even, the window that finds an opaque pixel is not 7 wide or wraps
    # at the edge, a pixel of alpha 200 counts as opaque, or a full halo divides by zero, so a face's edge pixel is
    # one level off. (The strength of 6 saturates the halo; the mostly clear image has pixels far from any opaque.)
    seed, size, clear, radius, strength = case
    assert _sha(imaging.unglowed(_mottled(seed, *size, clear=clear), radius, strength)) == UNGLOWED[case]


FACES = {(0, False): "48700b8cd6fa5e1e", (1.0, True): "be62f5259f03cd33"}  # (ink, optical): digest of the face


@pytest.mark.parametrize("case", FACES)
def test_a_face_read_from_a_row_is_the_one_the_old_loops_read(case) -> None:
    # Bug: a glyph is cut from the wrong fill's pixels or box, or the ink gap between two letters is off by a
    # column, so the face's glyphs or spacing change.
    ink, optical = case
    face = faces.Face([(_letters(), "ace mn", faces.FILLS["yellow"], 3, None)], ink, optical)
    state = {"glyphs": {c: (_sha(i), i.size, dx, dy) for c, (i, dx, dy) in sorted(face.glyphs.items())},
             "rows": {k: (_sha(r), b) for k, (r, b) in face.rows.items()}, "origin": sorted(face.origin.items()),
             "right": sorted(face.right.items()), "left": sorted(face.left.items()), "space": face.space,
             "gap": getattr(face, "gap", None), "gaps": sorted(getattr(face, "gaps", {}).items())}
    assert _digest(state) == FACES[case]


def test_only_strokes_keeps_the_pixels_of_the_strokes_it_names() -> None:
    # Bug: a kept stroke's share loses the pixels another share's box overlaps, or the slice of strokes is off by one.
    row = _mottled(13, 16, 9, clear=0.25)
    assert {repr(strokes): _sha(build.only_strokes(row, {"fill": "yellow", "strokes": strokes}))
            for strokes in ([1, 4], 2)} == {"[1, 4]": "148c2e24b33e01e5", "2": "3fcf3b896d70cfc1"}


def test_ink_rows_are_the_bands_of_rows_with_alpha_over_16() -> None:
    # Bug: a row of alpha 16 counts as ink, or a band's end is off by one, so a face's row is cropped a row short.
    assert faces.ink_rows(_banded()) == [(1, 3), (5, 7), (9, 12)]
