"""Export this machine's input paths, so tests that need stock or donor files run instead of skipping.

The paths live in the `[tests]` table of `work/harness.toml` (ignored, per machine; `WOZMOD_HARNESS_CONFIG`
names another file). A variable already set in the environment wins.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG = Path(os.environ.get("WOZMOD_HARNESS_CONFIG", Path(__file__).resolve().parents[1] / "work/harness.toml"))

if CONFIG.is_file():
    for name, value in tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("tests", {}).items():
        os.environ.setdefault(name, str(value))
