from __future__ import annotations

from PIL import Image

from wozmod.art import build


def test_header_compaction_does_not_move_other_rows_or_clip_alpha_origin(monkeypatch, tmp_path):
    original_temp = build.tempfile.TemporaryDirectory
    monkeypatch.setattr(build.tempfile, "TemporaryDirectory", lambda: original_temp(dir=tmp_path))
    monkeypatch.setattr(build, "COMPACT_HEADER", {(4060, 33, 3): ("Alder horizon", 0.75, 1.5)})
    fill = (214, 192, 116, 255)
    served = Image.new("RGBA", (256, 224))
    for row in range(4):
        served.paste(fill, (4, row * 56 + 14, 100, row * 56 + 46))

    def rendered_label(text, scale, output, raster, *options):
        hscale = float(options[options.index("--hscale") + 1]) if "--hscale" in options else 1
        width = round(200 * hscale)
        image = Image.new("RGBA", (width + 4, 40))
        image.paste(fill, (2, 4, width + 2, 36))
        return image, {"ink": [2, 4, width + 2, 36], "baseline": 36}

    monkeypatch.setattr(build, "header_label", rendered_label)
    edit = {"entry": 4060, "item": 33, "face": "header",
            "bands": [(56 * row, 56 * (row + 1)) for row in range(4)],
            "rows": [[{"donor": [493, 13, 0], "row": row}] for row in range(4)]}
    texts = [None, None, None, "Alder horizon"]
    compact = build.vector_art(edit, served, texts, 2)
    ordinary = build.vector_art(dict(edit, entry=4080, item=38), served, texts, 2)
    untouched = served.crop((0, 0, 256, 168)).resize((512, 336), Image.Resampling.NEAREST)
    assert compact.crop((0, 0, 512, 336)).tobytes() == untouched.tobytes()
    assert ordinary.crop((0, 0, 512, 336)).tobytes() == untouched.tobytes()
    assert compact.crop((0, 336, 512, 448)).getbbox() == (3, 60, 153, 92)
    assert ordinary.crop((0, 336, 512, 448)).getbbox() == (8, 60, 208, 92)
