"""Menu batch rendering support."""
import argparse
import json
import sys
from pathlib import Path

from .menu import render
from .menu_styles import style_for


def render_requests(requests: list[dict], output_dir: Path) -> list[dict]:

    if not isinstance(requests, list):
        raise ValueError("menu requests must be a JSON list")
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            raise ValueError(f"menu request {index} must be an object")
        if not isinstance(request.get("text"), str):
            raise ValueError(f"menu request {index} text must be a string")
        if not isinstance(request.get("style"), str):
            raise ValueError(f"menu request {index} style must be a string")
        if request.get("role") is not None and not isinstance(request["role"], str):
            raise ValueError(f"menu request {index} role must be a string or null")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, request in enumerate(requests):
        style = style_for(request["style"], role=request.get("role"))
        style["scale"] = 1.0
        image = render(request["text"], style)
        filename = f"{index:06d}.png"
        image.save(output_dir / filename)
        manifest.append({"request": request, "index": index, "file": filename,
                         "metrics": dict(image.info), "size": list(image.size)})
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("requests", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    try:
        requests = json.loads(args.requests.read_text(encoding="utf-8"))
        manifest = render_requests(requests, args.output_dir)
    except (OSError, TypeError, ValueError) as error:
        print(f"menu batch: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"count": len(manifest),
                      "manifest": str(args.output_dir / "manifest.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
