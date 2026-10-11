"""The optional `wozmod-native` package: fast LZP2 and DXT5 codecs.

`module` is None when the package is not installed, or when `WOZMOD_NATIVE=0`
asks for the Python codecs. Both give the same bytes.
"""

from __future__ import annotations

import os

try:
    if os.environ.get("WOZMOD_NATIVE") == "0":
        raise ImportError("WOZMOD_NATIVE=0")
    import wozmod_native as module
except ImportError:
    module = None
