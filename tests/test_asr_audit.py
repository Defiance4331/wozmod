"""tools/asr_audit.py's verdicts on synthetic contexts and transcripts (no model, no game files)."""

from __future__ import annotations

from types import SimpleNamespace

import asr_audit


def _context(where: str, text: str, voice, neighbours=()):
    return SimpleNamespace(id=where, text=text, voice=voice, key=where.encode(), neighbours=neighbours)


def test_a_wordless_neighbour_is_no_rival_but_a_worded_one_is() -> None:
    # Bug: an empty transcript scored 1.0 against a "..." neighbour (two empty strings), so every clip Whisper heard
    # nothing in was reported as playing that neighbour's line. A neighbour with words must still be caught.
    lines = [_context("s.1", "Hold the gate until the scouts return.", ("d", 1), ("s.0", "s.2")),
             _context("s.2", "...", None, ("s.1", "s.3")),
             _context("s.3", "The river crossing is ours, march on.", ("d", 3), ("s.2", "s.4")),
             _context("s.4", "Fall back to the eastern camp at once!", None, ("s.3", "s.5"))]
    heard = {"s.1": "", "s.3": "Fall back to the eastern camp at once!"}
    found = asr_audit.verdicts(lines, lambda context: heard[context.id])
    assert {where: kind for where, (kind, _, _) in found.items()} == {"s.1": "unclear", "s.3": "crossed"}
    assert found["s.3"][1] == "s.4"
