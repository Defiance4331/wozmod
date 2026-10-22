from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from wozmod.art import build, lettering, vector
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
               "raster": vector.VALUE_RASTER, "options": ["--look", "value_dim"]}
    identity = lettering.key(request)
    encoded = dict(request, text={"text_sha256": _digest("ALPHA")})
    squish = {"kind": "squish", "base": identity, "width": 3, "fill": [2, 6]}
    return recipe, identity, request, {"format_version": 2, "metrics_only": [],
                                     "requests": {identity: encoded, lettering.key(squish): squish}}


def test_render_cannot_drop_recipe_key_text_or_squish_with_raw_ink_instead_of_measured_fill(tmp_path, monkeypatch, letters):
    # Also: the release bundle ships pixels compose only measures, or the full-width
    # oracle bundle drops them.
    recipe, identity, request, document = _recipe(tmp_path, monkeypatch)
    plain = {"kind": "squish", "base": identity, "width": 3, "fill": None}
    document["requests"][lettering.key(plain)] = plain
    document["metrics_only"] = [identity]
    recipe.write_text(json.dumps(document))
    observed = []

    def synthetic(request):
        observed.append(request)
        image = Image.new("RGBA", (8, 2))
        image.putdata([(30 * x, 0, 0, 255) for _ in range(2) for x in range(8)])
        return image, {
            "baseline": 2, "ink": [1, 0, 7, 2],
            "pen": 1, "advance": 8, "size": [8, 2], "_lettering": identity, "counterpart": "BETA",
        }

    letters(synthetic, native=lambda requests: [], outlines=lambda folder: b"outlines")  # no FontForge here
    path = tmp_path / "letters.zip"
    result = renderer.render(path, jobs=2)
    layers, outlines = read_bundle(path, hashlib.sha256(recipe.read_bytes()).hexdigest())
    assert outlines == b"outlines"
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
    full, _ = read_bundle(tmp_path / "full.zip", hashlib.sha256(recipe.read_bytes()).hexdigest())
    assert list(full[identity][0].get_flattened_data()) == [(30 * x, 0, 0, 255) for _ in range(2) for x in range(8)]


@pytest.mark.parametrize("bug, message", [
    ("unresolved_text", "unresolved text SHA-256"),
    ("changed_request", "request SHA-256 mismatch"),
    ("wrong_version", "format version"),
    ("missing_full_width_base", "no full-width base"),
    ("duplicate_request_key", "duplicate lettering recipe key"),
])
def test_render_cannot_ship_a_changed_or_unresolvable_recipe(tmp_path, monkeypatch, letters, bug, message):
    recipe, identity, _, document = _recipe(tmp_path, monkeypatch)
    letters(lambda request: (Image.new("RGBA", (8, 2)), {"baseline": 2, "ink": [1, 0, 7, 2], "fill": [1, 7]}),
            native=lambda requests: [], outlines=lambda folder: b"outlines")
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


def test_traced_build_cannot_ship_a_full_width_label_it_draws_without_pixels(tmp_path, monkeypatch, letters):
    # Bug: a label drawn at full width is recorded as measured only (compose then fails),
    # or reading a base inside its squish keeps that base's pixels in the release bundle.
    source = tmp_path / "text.json"
    source.write_text(json.dumps(["FITS", "OVERFLOWS"]))
    monkeypatch.setattr(renderer, "TEXT_SOURCES", (source,))
    monkeypatch.setattr(renderer, "RECIPE", tmp_path / "recipe.json")
    monkeypatch.setattr(lettering, "TRACE", str(tmp_path / "trace"))
    def drawn(request):
        image = Image.new("RGBA", (12, 4))
        image.paste((255, 255, 255, 255), (1, 0, 11, 4))
        return image, {"ink": [1, 0, 11, 4], "baseline": 4}

    letters(drawn)
    bases = {}
    for text in ("FITS", "OVERFLOWS"):
        bases[text] = lettering.key({"kind": "header", "text": text, "scale": 1, "raster": vector.HEADER_RASTER,
                                     "options": []})
        label, metrics = lettering.header(text, 1, vector.HEADER_RASTER)
        label, metrics = lettering.squeezed(label, metrics, (0, 20 if text == "FITS" else 5, 4), 1, True)
        Image.new("RGBA", (40, 8)).paste(label, (2, 2))
    renderer.write_recipe([tmp_path / "trace"])
    _, _, metrics_only = renderer._load_requests()
    assert metrics_only == {bases["OVERFLOWS"]}


def _cached_recipe(tmp_path, monkeypatch):
    """A recipe of two headers, three native labels and a squish, and a producer whose layers differ by request."""
    texts = ["ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON"]
    source = tmp_path / "text.json"
    source.write_text(json.dumps(texts))
    monkeypatch.setattr(renderer, "TEXT_SOURCES", (source,))
    recipe = tmp_path / "recipe.json"
    monkeypatch.setattr(renderer, "RECIPE", recipe)
    plain = [{"kind": "header", "text": text, "scale": 2, "raster": vector.VALUE_RASTER, "options": []}
             for text in texts[:2]]
    plain += [{"kind": "native", "text": text, "style": "sound.sound"} for text in texts[2:]]
    plain.append({"kind": "squish", "base": lettering.key(plain[0]), "width": 3, "fill": None})
    recipe.write_text(json.dumps({"format_version": 2, "metrics_only": [], "requests": {
        lettering.key(request): {name: {"text_sha256": _digest(value)} if name == "text" else value
                                 for name, value in request.items()} for request in plain}}))

    def layer(text, scale):
        image = Image.new("RGBA", (len(text) + scale, 3))
        image.putdata([(len(text) * 20 + x, scale, 0, 255) for _ in range(3) for x in range(image.width)])
        return image, {"baseline": 3, "ink": [0.5, 0, len(text) + 0.25, 3], "text": text}

    drawn = []

    def produce(request):
        drawn.append(request["text"])
        return layer(request["text"], request["scale"])

    def native(requests):
        drawn.extend(request["text"] for request in requests)
        return [layer(request["text"], 1) for request in requests]

    return plain, drawn, dict(produce=produce, native=native, outlines=lambda folder: b"outlines")


def test_a_render_from_a_partial_layer_cache_ships_the_bundle_a_full_render_does(tmp_path, monkeypatch, letters):
    """Bug: a render that takes some layers from the cache pairs the native labels it still draws with the wrong
    requests, or a cached layer comes back with other pixels, mode or metrics, so the bundle differs from an
    uncached render's; or it redraws layers the cache holds."""
    plain, drawn, producer = _cached_recipe(tmp_path, monkeypatch)
    letters(producer.pop("produce"), **producer)
    monkeypatch.setattr(renderer, "renderer_key", lambda python: "renderer")
    cache = tmp_path / "cache"
    renderer.render(tmp_path / "uncached.zip", jobs=2)
    expected = (tmp_path / "uncached.zip").read_bytes()
    runs = {}
    for name in ("cold", "warm", "partial"):
        if name == "partial":  # the first header and the middle native label, and the outlines
            for request in (plain[0], plain[3]):
                (cache / "renderer" / f"{lettering.key(request)}.json").unlink()
            (cache / "renderer" / renderer.LayerCache.OUTLINES).unlink()
        drawn.clear()
        renderer.render(tmp_path / f"{name}.zip", jobs=2, cache=cache)
        runs[name] = ((tmp_path / f"{name}.zip").read_bytes() == expected, sorted(drawn))
    assert runs == {"cold": (True, ["ALPHA", "BETA", "DELTA", "EPSILON", "GAMMA"]), "warm": (True, []),
                    "partial": (True, ["ALPHA", "DELTA"])}


def test_the_layer_cache_key_changes_with_each_renderer_input(tmp_path, monkeypatch):
    """Bug: a renderer input drops out of the cache key, so a render after a change to the font code, the face
    sources, the font Python or the system libraries serves layers the old renderer drew."""
    repo, python, packages = tmp_path / "repo", tmp_path / "python", tmp_path / "rpmdb.sqlite"
    paths = [repo / "src/wozmod/fonts/menu.py", repo / "src/wozmod/art/lettering.py",
             repo / "src/wozmod/runtime/latin_font.py", repo / "data/art/faces/menusans.py"]
    for path in [*paths, packages]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("a")
    python.write_text("#!/bin/sh\necho fontforge 20251009\n")
    python.chmod(0o755)
    monkeypatch.setattr(renderer.build, "REPO", repo)
    monkeypatch.setattr(renderer, "SYSTEM_PACKAGES", (packages,))

    def later(path):
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))

    edits = {**{path.relative_to(repo).as_posix(): lambda path=path: path.write_text("b") for path in paths},
             "a new font module": lambda: (repo / "src/wozmod/fonts/new.py").write_text("a"),
             "font Python libraries": lambda: python.write_text("#!/bin/sh\necho fontforge 20260101\n"),
             "system package update": lambda: later(packages)}
    unchanged = []
    for name, edit in edits.items():
        before = renderer.renderer_key(str(python))
        edit()
        if renderer.renderer_key(str(python)) == before:
            unchanged.append(name)
    assert unchanged == []


def test_everything_the_font_python_imports_is_in_the_layer_cache_key():
    """Bug: a font module imports a module outside RENDERER_SOURCES, so an edit to that module leaves cached layers
    drawn by the old code."""
    python = lettering.FONT_PYTHON
    probe = subprocess.run([python, "-c", "import cairo, fontforge, numpy"], capture_output=True) \
        if Path(python).is_file() else None
    if probe is None or probe.returncode:
        pytest.skip("font Python with FontForge, Cairo and NumPy absent")
    # every font module but menu_references, which the art build reads donor textures with and the font Python never runs
    fonts = sorted(p.stem for p in (build.REPO / "src/wozmod/fonts").glob("*.py")
                   if p.stem not in {"__init__", "menu_references"})
    script = ("import importlib, sys\n"
              f"for name in {['wozmod.art.lettering', *(f'wozmod.fonts.{stem}' for stem in fonts)]!r}:\n"
              "    importlib.import_module(name)\n"
              "print('\\n'.join(sorted(m.__file__ for m in list(sys.modules.values()) if getattr(m, '__file__', None))))")
    run = subprocess.run([python, "-c", script], capture_output=True, text=True, env=lettering.font_environment())
    assert run.returncode == 0, run.stderr[-2000:]
    roots = [build.REPO / root for root in renderer.RENDERER_SOURCES]
    ours = [Path(f) for f in run.stdout.split() if Path(f).is_relative_to(build.REPO / "src")]
    outside = [f.relative_to(build.REPO).as_posix() for f in ours
               if f.name != "__init__.py" and not any(f == root or f.is_relative_to(root) for root in roots)]
    assert outside == []
