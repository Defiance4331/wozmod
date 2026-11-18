"""The `wozmod-native` package: fast LZP2 and DXT5 codecs, the audio alignment search, the learned mouth shapes and
the lettering faces' pixel loops.

`module` is None when the package is not installed, or when `WOZMOD_NATIVE=0`
asks for the Python codecs. Both give the same bytes. The audio alignment, the
mouths, the stage scripts' stream alignments and the pixel loops have no Python
twin: the voices step needs the package for the Dream movies and scenes and for
the event lines' mouths, the text and voices steps for the stage joins, and the
art step to read the lettering faces.
"""

from __future__ import annotations

import os

try:
    if os.environ.get("WOZMOD_NATIVE") == "0":
        raise ImportError("WOZMOD_NATIVE=0")
    import wozmod_native as module
except ImportError:
    module = None
