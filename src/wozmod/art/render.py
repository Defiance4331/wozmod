"""Render the authored lettering recipes into a release bundle."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image

from . import build, lettering
from .bundle import write_bundle


RECIPE = build.REPO / "data/art/lettering.json"
RECIPE_VERSION = 2
TEXT_SOURCES = (build.REPO / "data/art/menu_textures.json",
                build.REPO / "data/art/menu_visuals.json")
_RASTERS = {build.HEADER_RASTER, build.VALUE_RASTER, build.MAIN_RASTER,
            build.LABEL_RASTER, build.MENU_RASTER}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item)


def _text_index() -> dict[str, str]:
    values = [json.loads(path.read_text(encoding="utf-8")) for path in TEXT_SOURCES]
    values.extend(value for name, value in vars(build).items() if name.isupper())
    return {hashlib.sha256(text.encode("utf-8")).hexdigest(): text
            for value in values for text in _strings(value)}


def _decode(value, texts: dict[str, str]):
    if isinstance(value, dict):
        if set(value) == {"text_sha256"}:
            digest = value["text_sha256"]
            if not isinstance(digest, str) or digest not in texts:
                raise ValueError(f"lettering recipe has unresolved text SHA-256: {digest}")
            return texts[digest]
        return {key: _decode(item, texts) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item, texts) for item in value]
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate lettering recipe key: {key}")
        result[key] = value
    return result


def _number(request: dict, name: str, positive: bool = False) -> None:
    value = request[name]
    if (type(value) not in (int, float) or not math.isfinite(value)
            or positive and value <= 0):
        raise ValueError(f"invalid lettering request {name}")


def _validate(request: dict) -> None:
    fields = {
        "header": {"kind", "text", "scale", "raster", "options"},
        "button": {"kind", "text", "scale", "track", "style"},
        "pause": {"kind", "text", "scale", "target", "counterpart"},
        "native": {"kind", "text", "style"},
        "latin": {"kind"},
        "squish": {"kind", "base", "width", "fill"},
    }
    kind = request.get("kind")
    if not isinstance(kind, str) or kind not in fields:
        raise ValueError("unsupported lettering request kind")
    expected = fields[kind] | ({"role"} if kind == "native" and "role" in request else set())
    if set(request) != expected:
        raise ValueError(f"invalid {kind} lettering request fields")
    if "text" in request and not isinstance(request["text"], str):
        raise ValueError("lettering request text must be a string")
    for name in ("scale", "target", "width"):
        if name in request:
            _number(request, name, positive=True)
    if "track" in request:
        _number(request, "track")
    if "style" in request and not isinstance(request["style"], str):
        raise ValueError("lettering request style must be a string")
    if kind == "header":
        if request["raster"] not in _RASTERS:
            raise ValueError("unsupported lettering rasterizer")
        if (not isinstance(request["options"], list)
                or any(not isinstance(item, str) for item in request["options"])):
            raise ValueError("lettering request options must be strings")
    if kind == "pause" and request["counterpart"] is not None and not isinstance(request["counterpart"], str):
        raise ValueError("lettering request counterpart must be a string or null")
    if kind == "native" and request.get("role") is not None and not isinstance(request["role"], str):
        raise ValueError("lettering request role must be a string or null")
    if kind == "squish" and not isinstance(request["base"], str):
        raise ValueError("lettering request base must be a request SHA-256")
    if kind == "squish" and request["fill"] is not None:
        fill = request["fill"]
        if (not isinstance(fill, list) or len(fill) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in fill)
                or fill[1] <= fill[0]):
            raise ValueError("lettering request fill must be two finite increasing endpoints or null")


def _load_requests() -> tuple[dict[str, dict], str, frozenset[str]]:
    payload = RECIPE.read_bytes()
    recipe = json.loads(payload, object_pairs_hook=_unique)
    if not isinstance(recipe, dict) or set(recipe) != {"format_version", "requests", "metrics_only"}:
        raise ValueError("invalid lettering recipe fields")
    if type(recipe["format_version"]) is not int or recipe["format_version"] != RECIPE_VERSION:
        raise ValueError("unsupported lettering recipe format version")
    if not isinstance(recipe["requests"], dict):
        raise ValueError("lettering recipe requests must be an object")
    metrics_only = recipe["metrics_only"]
    if not isinstance(metrics_only, list) or not set(metrics_only) <= set(recipe["requests"]):
        raise ValueError("lettering recipe metrics_only names an unknown request")
    texts = _text_index()
    requests = {}
    for identity, encoded in recipe["requests"].items():
        request = _decode(encoded, texts)
        if not isinstance(request, dict):
            raise ValueError("lettering recipe request must be an object")
        _validate(request)
        if lettering.key(request) != identity:
            raise ValueError(f"lettering recipe request SHA-256 mismatch: {identity}")
        requests[identity] = request
    for request in requests.values():
        if request["kind"] == "squish":
            base = requests.get(request["base"])
            if base is None or base["kind"] in {"squish", "latin"}:
                raise ValueError("squished lettering request has no full-width base")
    return requests, hashlib.sha256(payload).hexdigest(), frozenset(metrics_only)


def write_recipe(traces: list[Path]) -> int:
    """Write the recipe from the requests a traced build recorded (WOZMOD_ART_TRACE).

    A request whose pixels no traced build drew outside a squish ships metrics only.
    """
    texts = {text: digest for digest, text in _text_index().items()}
    requests, drawn = {}, set()
    for trace in traces:
        drawn |= {path.stem for path in Path(trace).glob("*.pixels")}
        for path in sorted(Path(trace).glob("*.json")):
            request = json.loads(path.read_text(encoding="utf-8"))
            _validate(request)
            if lettering.key(request) != path.stem:
                raise ValueError(f"traced request does not match its name: {path.name}")
            encoded = dict(request)
            for name in ("text", "counterpart"):
                if isinstance(request.get(name), str):
                    if request[name] not in texts:
                        raise ValueError(f"traced {name} is not in the art recipes: {path.stem}")
                    encoded[name] = {"text_sha256": texts[request[name]]}
            requests[path.stem] = encoded
    rows = ",\n".join(f"{json.dumps(key)}: {json.dumps(requests[key], sort_keys=True)}"
                      for key in sorted(requests))
    metrics_only = ",\n".join(json.dumps(key) for key in sorted(set(requests) - drawn))
    RECIPE.write_text(f'{{"format_version": {RECIPE_VERSION}, "metrics_only": [\n{metrics_only}\n], '
                      '"requests": {\n' + rows + "\n}}\n", encoding="utf-8")
    _load_requests()
    return len(requests)


def _render_full(identity: str, request: dict, folder: Path) -> tuple[Image.Image, dict]:
    kind = request["kind"]
    output = folder / f"{identity}.png"
    if kind == "header":
        return build._render_header_label(request["text"], request["scale"], output,
                                          request["raster"], *request["options"])
    if kind == "button":
        return build._render_button_label(request["text"], request["scale"], output,
                                          request["track"], request["style"])
    if kind == "pause":
        return build._render_pause_button_label(request["text"], request["scale"], output,
                                                request["target"], request["counterpart"])
    if kind == "latin":
        subprocess.run([build.FONT_PYTHON, "-m", "wozmod.fonts.raster", str(output),
                        "--px", "24", "--spacing", "legacy"],
                       check=True, capture_output=True, text=True, encoding="utf-8", env=build.font_environment())
        with Image.open(output) as image:
            return image.convert("L"), {}
    raise ValueError(f"unsupported full-width lettering request: {kind}")


def _render_native(requests: dict[str, dict], folder: Path) -> dict[str, tuple]:
    ordered = [(identity, {name: value for name, value in request.items() if name != "kind"})
               for identity, request in sorted(requests.items()) if request["kind"] == "native"]
    if not ordered:
        return {}
    path, output = folder / "native-requests.json", folder / "native"
    path.write_text(json.dumps([request for _, request in ordered], ensure_ascii=False), encoding="utf-8")
    subprocess.run([build.FONT_PYTHON, "-m", build.MENU_BATCH, str(path), str(output)],
                   check=True, capture_output=True, text=True, encoding="utf-8", env=build.font_environment())
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if len(manifest) != len(ordered):
        raise ValueError("native lettering render dropped a requested layer")
    layers = {}
    for index, (record, (identity, request)) in enumerate(zip(manifest, ordered, strict=True)):
        if record["index"] != index or record["request"] != request:
            raise ValueError("native lettering render changed a requested layer")
        with Image.open(output / record["file"]) as image:
            layers[identity] = image.convert("RGBA"), record["metrics"]
    return layers


def render(bundle: str | Path, jobs: int = 4, font_python: str | None = None,
           full_width: bool = False) -> dict:
    """Render the recipe without loading stock or donor art.

    The release bundle leaves out the pixels of layers compose only measures; full_width
    keeps every layer for the developer squish=False oracle.
    """
    if type(jobs) is not int or jobs < 1:
        raise ValueError("render jobs must be a positive integer")
    started = time.perf_counter()
    requests, recipe_sha256, metrics_only = _load_requests()
    output = Path(bundle)
    output.parent.mkdir(parents=True, exist_ok=True)
    previous_python = build.FONT_PYTHON
    if font_python is not None:
        build.FONT_PYTHON = font_python
    try:
        with tempfile.TemporaryDirectory(prefix="lettering-render-", dir=output.parent) as temporary:
            folder, layers = Path(temporary), {}
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = {pool.submit(_render_full, identity, request, folder): identity
                           for identity, request in requests.items()
                           if request["kind"] not in {"squish", "native"}}
                native = pool.submit(_render_native, requests, folder)
                for future in as_completed(futures):
                    layers[futures[future]] = future.result()
                layers.update(native.result())
            for identity, request in requests.items():
                if request["kind"] != "squish":
                    continue
                image, metrics = layers[request["base"]]
                metrics = {name: value for name, value in metrics.items() if name != "_lettering"}
                if request["fill"] is not None:
                    metrics["fill"] = request["fill"]
                layers[identity] = build._squeezed_label(image, metrics, (0, request["width"], 0), 1, True)
            layers = {identity: (image, {name: value for name, value in metrics.items()
                                        if name not in {"_lettering", "counterpart"}})
                      for identity, (image, metrics) in layers.items()}
            if set(layers) != set(requests):
                raise ValueError("rendered lettering layers do not match the recipe")
            write_bundle(output, layers, recipe_sha256, frozenset() if full_width else metrics_only)
    finally:
        build.FONT_PYTHON = previous_python
    return {"layers": len(requests), "bytes": output.stat().st_size,
            "seconds": time.perf_counter() - started}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_bundle", type=Path, nargs="?")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--font-python", default=build.FONT_PYTHON)
    parser.add_argument("--full-width", action="store_true",
                        help="developer oracle bundle: keep every full-width layer (not for release)")
    parser.add_argument("--recipe-from-trace", type=Path, nargs="+", metavar="TRACE",
                        help="rewrite data/art/lettering.json from traced builds instead of rendering")
    args = parser.parse_args(argv)
    if args.recipe_from_trace:
        print(json.dumps({"requests": write_recipe(args.recipe_from_trace)}))
        return 0
    if args.output_bundle is None:
        parser.error("give the output bundle path")
    result = render(args.output_bundle, jobs=args.jobs, font_python=args.font_python,
                    full_width=args.full_width)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
