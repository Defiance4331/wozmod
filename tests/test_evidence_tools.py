"""The evidence helpers in tools/: montage (images side by side) and video_frames (stills from a recording)."""

import shutil
import subprocess

import pytest
from PIL import Image

import montage
import video_frames


def test_montage_keeps_every_image_in_order_in_its_cell(tmp_path):
    """Bug: an image is dropped, or two swap cells, so a before/after sheet shows the pair the wrong way round."""
    colours = ["red", "lime", "blue"]
    items = []
    for colour in colours:
        path = tmp_path / f"{colour}.png"
        Image.new("RGB", (64, 32), colour).save(path)
        items.append((path, colour))
    sheet = Image.open(montage.montage(items, tmp_path / "sheet.jpg", cols=2, width=64))
    cell = 32 + montage.LABEL_HEIGHT
    assert sheet.size == (128, 2 * cell)
    centres = [(32 + 64 * (n % 2), n // 2 * cell + montage.LABEL_HEIGHT + 16) for n in range(3)]
    expected = [Image.new("RGB", (1, 1), c).getpixel((0, 0)) for c in colours]
    for (x, y), want in zip(centres, expected, strict=True):
        assert all(abs(a - b) < 40 for a, b in zip(sheet.getpixel((x, y)), want, strict=True))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_still_shows_the_frame_at_its_time(tmp_path):
    """Bug: the seek lands on the wrong frame (a second early, or the first frame every time), so a subtitle is judged
    against the wrong moment of the movie."""
    video = tmp_path / "clip.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64:d=1:r=30", "-f", "lavfi", "-i",
                    "color=blue:s=64x64:d=1:r=30", "-filter_complex", "[0][1]concat=n=2", str(video)], check=True)
    early, late = video_frames.stills(video, [0.5, 1.5], tmp_path / "out")
    red, blue = (Image.open(path).convert("RGB").getpixel((32, 32)) for path in (early, late))
    assert red[0] > 200 > red[2] and blue[2] > 200 > blue[0]


def test_the_speech_check_scores_words_in_order_and_ignores_case_and_punctuation():
    """Bugs: a transcript that differs from the subtitle only in capitals or punctuation fails, or one with the right
    words in another order (a different line) passes."""
    import asr_check

    assert asr_check.match("None are fit to face me!", "none are fit to face me") == 1.0
    assert asr_check.match("me face to fit are none", "none are fit to face me") < 0.8
    assert asr_check.match("none are fit", "none are fit to face me") == 0.5
