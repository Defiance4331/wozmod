from __future__ import annotations

import hashlib
import importlib
import json

import pytest
from PIL import Image

from wozmod.art import build, lettering
from wozmod.art.bundle import MetricsOnly, read_bundle


renderer = importlib.import_module("wozmod.art.render")


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _recipe(tmp_path, monkeypatch):
    source = tmp_path / "text.json"
    source.write_text(json.dumps({"ALPHA": 1}))
    monkeypatch.setattr(renderer, "TEXT_SOURCES", (source,))
    recipe = tmp_path / "recipe.json"
    monkeypatch.setattr(renderer, "RECIPE", recipe)
    request = {"kind": "header", "text": "ALPHA", "scale": 2,
               "raster": build.VALUE_RASTER, "options": ["--look", "value_dim"]}
    identity = lettering.key(request)
    encoded = dict(request, text={"text_sha256": _digest("ALPHA")})
    squish = {"kind": "squish", "base": identity, "width": 3, "fill": [2, 6]}
    return recipe, identity, request, {"format_version": 2, "metrics_only": [],
                                     "requests": {identity: encoded, lettering.key(squish): squish}}


def test_render_cannot_drop_recipe_key_text_or_squish_with_raw_ink_instead_of_measured_fill(tmp_path, monkeypatch):
    # Also: the release bundle ships pixels compose only measures, or the full-width
    # oracle bundle drops them.
    recipe, identity, request, document = _recipe(tmp_path, monkeypatch)
    plain = {"kind": "squish", "base": identity, "width": 3, "fill": None}
    document["requests"][lettering.key(plain)] = plain
    document["metrics_only"] = [identity]
    recipe.write_text(json.dumps(document))
    observed = []

    def synthetic(text, scale, output, raster, *options):
        observed.append({"kind": "header", "text": text, "scale": scale,
                         "raster": raster, "options": list(options)})
        image = Image.new("RGBA", (8, 2))
        image.putdata([(30 * x, 0, 0, 255) for _ in range(2) for x in range(8)])
        return image, {
            "baseline": 2, "ink": [1, 0, 7, 2],
            "pen": 1, "advance": 8, "size": [8, 2], "_lettering": identity, "counterpart": "BETA",
        }

    monkeypatch.setattr(build, "_render_header_label", synthetic)
    path = tmp_path / "letters.zip"
    result = renderer.render(path, jobs=2)
    layers = read_bundle(path, hashlib.sha256(recipe.read_bytes()).hexdigest())
    squish_key = lettering.key({"kind": "squish", "base": identity, "width": 3, "fill": [2, 6]})
    actual = {key: (image.size, metrics) for key, (image, metrics) in layers.items()}
    assert observed == [request]
    assert result["layers"] == 3
    assert actual == {
        identity: ((8, 2), {"baseline": 2, "ink": [1, 0, 7, 2],
                           "pen": 1, "advance": 8, "size": [8, 2]}),
        squish_key: ((7, 2), {"baseline": 2, "ink": [1.25, 0, 5.75, 2], "fill": [2, 5],
                             "pen": 1.25, "advance": 6, "size": [7, 2]}),
        lettering.key(plain): ((5, 2), {"baseline": 2, "ink": [1, 0, 4, 2],
                                       "pen": 1, "advance": 4, "size": [5, 2]}),
    }
    assert list(layers[squish_key][0].get_flattened_data())[:7] == [
        (0, 0, 0, 255), (21, 0, 0, 255), (67, 0, 0, 255), (105, 0, 0, 255),
        (142, 0, 0, 255), (188, 0, 0, 255), (0, 0, 0, 0),
    ]
    assert isinstance(layers[identity][0], MetricsOnly)
    renderer.render(tmp_path / "full.zip", full_width=True)
    full = read_bundle(tmp_path / "full.zip", hashlib.sha256(recipe.read_bytes()).hexdigest())
    assert list(full[identity][0].get_flattened_data()) == [(30 * x, 0, 0, 255) for _ in range(2) for x in range(8)]


@pytest.mark.parametrize("bug, message", [
    ("unresolved_text", "unresolved text SHA-256"),
    ("changed_request", "request SHA-256 mismatch"),
    ("wrong_version", "format version"),
    ("missing_full_width_base", "no full-width base"),
    ("duplicate_request_key", "duplicate lettering recipe key"),
])
def test_render_cannot_ship_a_changed_or_unresolvable_recipe(tmp_path, monkeypatch, bug, message):
    recipe, identity, _, document = _recipe(tmp_path, monkeypatch)
    monkeypatch.setattr(build, "_render_header_label", lambda *args: (
        Image.new("RGBA", (8, 2)), {"baseline": 2, "ink": [1, 0, 7, 2], "fill": [1, 7]}))
    if bug == "unresolved_text":
        document["requests"][identity]["text"] = {"text_sha256": "0" * 64}
    elif bug == "changed_request":
        document["requests"][identity]["scale"] = 1
    elif bug == "wrong_version":
        document["format_version"] = 1
    elif bug == "missing_full_width_base":
        document["requests"].pop(identity)
    payload = json.dumps(document)
    if bug == "duplicate_request_key":
        encoded = json.dumps(document["requests"][identity])
        payload = '{"format_version":2,"metrics_only":[],"requests":{' + json.dumps(identity) + ':' + encoded + ',' + json.dumps(identity) + ':' + encoded + '}}'
    recipe.write_text(payload)
    path = tmp_path / "letters.zip"
    with pytest.raises(ValueError, match=message):
        renderer.render(path)
    assert not path.exists()


def test_recipe_from_trace_cannot_keep_raw_label_text_or_change_a_request(tmp_path, monkeypatch):
    # Bug: the traced label text is written verbatim into the tracked recipe, or the
    # encoded request no longer decodes to the request the build asked for.
    source = tmp_path / "text.json"
    source.write_text(json.dumps({"rows": ["QUIT GAME", "RESUME"]}))
    monkeypatch.setattr(renderer, "TEXT_SOURCES", (source,))
    monkeypatch.setattr(renderer, "RECIPE", tmp_path / "recipe.json")
    pause = {"kind": "pause", "text": "QUIT GAME", "scale": 2, "target": 61.5, "counterpart": "RESUME"}
    squish = {"kind": "squish", "base": lettering.key(pause), "width": 99.25, "fill": None}
    trace = tmp_path / "trace"
    trace.mkdir()
    for request in (pause, squish):
        (trace / f"{lettering.key(request)}.json").write_text(json.dumps(request))
    (trace / f"{lettering.key(squish)}.pixels").touch()
    assert renderer.write_recipe([trace]) == 2
    written = (tmp_path / "recipe.json").read_text()
    assert "QUIT GAME" not in written and "RESUME" not in written
    requests, _, metrics_only = renderer._load_requests()
    assert requests == {lettering.key(pause): pause, lettering.key(squish): squish}
    assert metrics_only == {lettering.key(pause)}


def test_traced_build_cannot_ship_a_full_width_label_it_draws_without_pixels(tmp_path, monkeypatch):
    # Bug: a label drawn at full width is recorded as measured only (compose then fails),
    # or reading a base inside its squish keeps that base's pixels in the release bundle.
    source = tmp_path / "text.json"
    source.write_text(json.dumps(["FITS", "OVERFLOWS"]))
    monkeypatch.setattr(renderer, "TEXT_SOURCES", (source,))
    monkeypatch.setattr(renderer, "RECIPE", tmp_path / "recipe.json")
    monkeypatch.setattr(lettering, "TRACE", str(tmp_path / "trace"))
    bases = {}
    for text in ("FITS", "OVERFLOWS"):
        request = {"kind": "header", "text": text, "scale": 1, "raster": build.HEADER_RASTER, "options": []}
        image = Image.new("RGBA", (12, 4))
        image.paste((255, 255, 255, 255), (1, 0, 11, 4))
        bases[text] = lettering.key(request)
        label, metrics = lettering.get(request, lambda image=image: (image, {"ink": [1, 0, 11, 4], "baseline": 4}))
        label, metrics = build.squeezed_label(label, metrics, (0, 20 if text == "FITS" else 5, 4), 1, True)
        Image.new("RGBA", (40, 8)).paste(label, (2, 2))
    renderer.write_recipe([tmp_path / "trace"])
    _, _, metrics_only = renderer._load_requests()
    assert metrics_only == {bases["OVERFLOWS"]}
