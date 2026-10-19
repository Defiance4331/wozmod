from __future__ import annotations

import hashlib
import io
import json
import zipfile
from contextlib import nullcontext

import pytest
from PIL import Image

from wozmod.art.bundle import read_bundle, write_bundle


RECIPE_SHA256 = "1" * 64
OUTLINES = b'{"glyphs":{}}'


def layers():
    mask = Image.new("L", (3, 2))
    mask.putdata([0, 17, 255, 31, 89, 143])
    color = Image.new("RGBA", (2, 1))
    color.putdata([(3, 5, 7, 11), (13, 17, 19, 23)])
    return {"mask/full": (mask, {"advance": 4.25, "origin": [-2, 3]}),
            "label/squish": (color, {"height": 1})}


def test_bundle_round_trip_cannot_drop_edge_pixels_or_layer_metrics(tmp_path):
    first = tmp_path / "bundle.zip"
    write_bundle(first, layers(), RECIPE_SHA256, outlines=OUTLINES)
    restored, outlines = read_bundle(first, RECIPE_SHA256)
    assert outlines == OUTLINES
    actual = {key: (image.mode, image.size, list(image.get_flattened_data()), metrics)
              for key, (image, metrics) in restored.items()}
    assert actual == {
        "mask/full": ("L", (3, 2), [0, 17, 255, 31, 89, 143],
                      {"advance": 4.25, "origin": [-2, 3]}),
        "label/squish": ("RGBA", (2, 1), [(3, 5, 7, 11), (13, 17, 19, 23)], {"height": 1}),
    }
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {
            "index.json", "glyph-outlines.json",
            "layers/" + hashlib.sha256(b"mask/full").hexdigest() + ".png",
            "layers/" + hashlib.sha256(b"label/squish").hexdigest() + ".png",
        }


def test_bundle_bytes_cannot_change_with_layer_input_order(tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    original = layers()
    write_bundle(first, original, RECIPE_SHA256, outlines=OUTLINES)
    write_bundle(second, dict(reversed(list(original.items()))), RECIPE_SHA256, outlines=OUTLINES)
    assert first.read_bytes() == second.read_bytes()


@pytest.mark.parametrize("bug, message", [
    ("dropped_layer", "members do not match"),
    ("misnamed_layer", "misnamed layer"),
    ("wrong_layer_hash", "layer SHA-256 mismatch"),
    ("wrong_format_version", "format version"),
    ("wrong_recipe_identity", "recipe SHA-256 mismatch"),
    ("extra_layer", "members do not match"),
    ("duplicate_zip_member", "duplicate bundle member"),
    ("duplicate_logical_key", "duplicate layer key"),
    ("wrong_declared_mode", "mode or size mismatch"),
    ("wrong_declared_size", "mode or size mismatch"),
    ("non_png_layer", "not a PNG"),
    ("missing_index", "missing bundle index"),
    ("duplicate_json_field", "duplicate JSON key"),
    ("boolean_format_version", "format version"),
    ("nonfinite_metric", "finite JSON values"),
    ("dropped_outlines", "members do not match"),
    ("wrong_outlines", "glyph outlines SHA-256 mismatch"),
])
def test_bundle_corruption_cannot_silently_ship_a_dropped_or_wrong_layer(tmp_path, bug, message):
    path = tmp_path / "bundle.zip"
    write_bundle(path, layers(), RECIPE_SHA256, outlines=OUTLINES)
    with zipfile.ZipFile(path) as archive:
        members = [(member.filename, archive.read(member)) for member in archive.infolist()]
    index = json.loads(members[0][1])
    row = index["layers"][0]
    if bug == "dropped_layer":
        members = [(name, data) for name, data in members if name != row["path"]]
    elif bug == "misnamed_layer":
        previous = row["path"]
        row["path"] = "layers/misnamed.png"
        members = [(row["path"] if name == previous else name, data) for name, data in members]
    elif bug == "wrong_layer_hash":
        row["sha256"] = "0" * 64
    elif bug == "wrong_format_version":
        index["format_version"] = 1
    elif bug == "wrong_recipe_identity":
        index["recipe_sha256"] = "2" * 64
    elif bug == "extra_layer":
        members.append(("layers/unlisted.png", members[1][1]))
    elif bug == "duplicate_zip_member":
        members.append(members[1])
    elif bug == "duplicate_logical_key":
        index["layers"].append(row.copy())
    elif bug == "wrong_declared_mode":
        row["mode"] = "RGB"
    elif bug == "wrong_declared_size":
        row["size"][0] += 1
    elif bug == "non_png_layer":
        stream = io.BytesIO()
        Image.new("RGB", (2, 1)).save(stream, format="BMP")
        data = stream.getvalue()
        row["sha256"] = hashlib.sha256(data).hexdigest()
        row["mode"], row["size"] = "RGB", [2, 1]
        members = [(name, data if name == row["path"] else payload) for name, payload in members]
    elif bug == "missing_index":
        members = members[1:]
    elif bug == "boolean_format_version":
        index["format_version"] = True
    elif bug == "nonfinite_metric":
        row["metrics"] = {"advance": float("nan")}
    elif bug == "dropped_outlines":
        members = [(name, data) for name, data in members if name != "glyph-outlines.json"]
    elif bug == "wrong_outlines":
        members = [(name, data + b" " if name == "glyph-outlines.json" else data) for name, data in members]
    payload = json.dumps(index).encode()
    if bug == "duplicate_json_field":
        payload = b'{"format_version":1,' + payload[1:]
    members = [(name, payload if name == "index.json" else data) for name, data in members]
    warning = pytest.warns(UserWarning, match="Duplicate name") if bug == "duplicate_zip_member" else nullcontext()
    with warning, zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    with pytest.raises(ValueError, match=message):
        read_bundle(path, RECIPE_SHA256)


def test_metrics_only_layer_cannot_be_drawn_as_blank_pixels(tmp_path):
    # Bug: a layer shipped without pixels composes silently as a transparent image.
    path = tmp_path / "bundle.zip"
    write_bundle(path, layers(), RECIPE_SHA256, frozenset({"mask/full"}), outlines=OUTLINES)
    restored, _ = read_bundle(path, RECIPE_SHA256)
    stub, metrics = restored["mask/full"]
    assert (stub.mode, stub.size, metrics) == ("L", (3, 2), {"advance": 4.25, "origin": [-2, 3]})
    with zipfile.ZipFile(path) as archive:
        assert len(archive.namelist()) == 3  # the index, one PNG and the outlines
    for read in (stub.getbbox, stub.tobytes, lambda: Image.new("L", (4, 4)).paste(stub, (0, 0))):
        with pytest.raises(ValueError, match="shipped without pixels"):
            read()
    assert list(restored["label/squish"][0].get_flattened_data()) == [(3, 5, 7, 11), (13, 17, 19, 23)]
