import hashlib
import json
import math
import os
import random
import struct
from pathlib import Path

import pytest

from wozmod.voices import phones
from wozmod.voices.phones import NUMBER, Listener, donor_lines, frame_phones, model_problem, shown_lines, spelled, words


def test_each_lip_frame_takes_the_phone_sounding_at_its_middle_with_its_neighbours():
    # Bugs: a lip frame read at its start or end (half a frame early or late) or a whole frame off, which costs about
    # 0.006 of Koei's shapes per frame; a phone's end counted as inside it; a phone that PocketSphinx splits in two
    # taken as its own neighbour; a filler not read as silence. Lip frame t's middle is (2t + 1) * 5 / 3 phone frames:
    # 1.67, 5, 8.33, 11.67, 15, 18.33, 21.67.
    segments = [("SIL", 0, 2), ("AA", 2, 3), ("B", 5, 4), ("B", 9, 2), ("+NSN+", 11, 4), ("D", 16, 3)]
    sil, aa, b, d = 0, NUMBER["AA"], NUMBER["B"], NUMBER["D"]
    assert list(frame_phones(segments, 7)) == [
        sil, sil, aa,  # 1.67: SIL [0, 2)
        aa, b, sil,  # 5: B starts there; B [5, 11) is one phone
        aa, b, sil,  # 8.33
        b, sil, d,  # 11.67: the noise [11, 15)
        0, 0, 0,  # 15: the gap [15, 16)
        sil, d, 0,  # 18.33: D [16, 19)
        0, 0, 0,  # 21.67: past the last phone
    ]


def test_a_line_is_said_as_the_dictionary_spells_its_words():
    # Bugs: words joined by a hyphen read as one word the dictionary lacks; a letter with an accent (the donors write
    # long vowels with a macron) or the game's punctuation read as part of a word; an apostrophe inside a word dropped
    # ("it's" is not "its"), or one around it kept.
    assert words("Well-met, 'friend'... it's Ōmi-ya!\n") == ["well", "met", "friend", "it's", "omi", "ya"]


def test_a_word_the_dictionary_lacks_is_said_by_letter_groups_longest_first():
    # Bugs: a two-letter group read as its letters (sh as S HH, ch as K HH), a vowel pair read as two vowels, a sound
    # said twice where two groups give the same phone; a character no rule reads stopping the word.
    assert {w: spelled(w) for w in ("shichou", "tsaiyu", "hmmph", "taaro", "ka2i")} == {
        "shichou": ["SH", "IY", "CH", "OW"],
        "taaro": ["T", "AA", "R", "OW"],
        "tsaiyu": ["T", "S", "AY", "Y", "UW"],
        "hmmph": ["HH", "M", "F"],
        "ka2i": ["K", "AA", "IY"],
    }


def _event(order, lines):
    """An event file of line records at 12 mod 16, as formats.scripts.event_lines reads them."""
    data = bytearray(12)
    for index, text in lines:
        size = len(text) + 1 + -(len(text) + 1) % 16
        data += b"\xff" * 4 + struct.pack(f"{order}HH", index, size) + text.ljust(size, b"\0")
        data += bytes(-(len(data) - 12) % 16)
    return bytes(data)


def test_a_donor_clip_says_its_donors_line_and_only_one_line():
    # Bugs: a line two event files give different words taking either, so the mouth follows words the clip does not
    # say; a line read with the other byte order; a Koei letter (0xD4, o with a macron) dropped or a line that is not
    # donor English kept.
    first = _event(">", [(3, b"Line three"), (4, b"Line four"), (5, b"Lord K\xd4ji"), (6, b"\x81\x40")])
    second = _event(">", [(3, b"Line three"), (4, b"Another four")])
    assert donor_lines([first, second], ">") == {3: "Line three", 5: "Lord Kōji"}


def test_a_dream_clip_says_what_the_game_shows_under_it(tmp_path, monkeypatch):
    # Bugs: the line's own row (keyed with its voice id) passed over for the row of its text alone, which the runtime
    # tries second, so the mouth follows another translation; a voice id off by the file's base; a line no row
    # translates given its Japanese.
    def key(raw):
        return hashlib.sha256(raw).hexdigest()

    rows = [{"key": key(b"same@V3000"), "source": "authored", "en": "Voiced"},
            {"key": key(b"same"), "source": "authored", "en": "Plain"},
            {"key": key(b"other"), "source": "authored", "en": "Other"}]
    (tmp_path / "data/text").mkdir(parents=True)
    (tmp_path / "data/text/table.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    monkeypatch.setattr(phones.resources, "root", lambda: tmp_path)

    class Resolver:
        def resolve_row(self, row):
            return row["en"]

    events = [(2998, _event("<", [(2, b"same"), (3, b"same"), (4, b"other"), (5, b"untranslated")]))]
    assert shown_lines(events, {3000, 3001, 3003}, Resolver()) == {3000: "Voiced", 3001: "Plain"}


def test_the_speech_model_must_be_the_pinned_files(tmp_path, monkeypatch):
    # Bug: a model file of the right size but other contents accepted, so the phones, and the mouths, silently differ
    # from what this release makes; a missing file found only when PocketSphinx fails mid-install.
    monkeypatch.setattr(phones, "MODEL", {"en-us/mdef": (3, hashlib.sha256(b"abc").hexdigest()),
                                          "dict": (2, hashlib.sha256(b"de").hexdigest())})
    (tmp_path / "en-us").mkdir()
    (tmp_path / "en-us/mdef").write_bytes(b"abc")
    assert "dict" in model_problem(tmp_path)
    (tmp_path / "dict").write_bytes(b"dx")
    assert "dict" in model_problem(tmp_path)
    (tmp_path / "dict").write_bytes(b"de")
    assert model_problem(tmp_path) is None


def _voice(seed, seconds=1.2):
    """16 kHz samples of a voice-like sound: a pitch pulse train through two moving formants, with pauses."""
    rng, out = random.Random(seed), []
    f1, f2, phase = 500.0, 1500.0, 0.0
    for i in range(int(phones.PHONE_RATE * seconds)):
        if i % 1600 == 0:
            f1, f2 = rng.uniform(300, 900), rng.uniform(900, 2500)
        voiced = (i // 4000) % 3 != 2
        phase += 2 * math.pi * 120 / phones.PHONE_RATE
        pulse = (math.sin(phase) > 0.95) * 0.6 if voiced else 0.0
        t = i / phones.PHONE_RATE
        out.append(pulse * (math.sin(2 * math.pi * f1 * t) + 0.5 * math.sin(2 * math.pi * f2 * t))
                   + rng.gauss(0, 0.01))
    return b"".join(struct.pack("<h", round(max(-1.0, min(1.0, x)) * 32000)) for x in out)


@pytest.mark.skipif(not os.environ.get("WOZMOD_SPEECH"), reason="set WOZMOD_SPEECH to the speech model folder")
def test_a_lines_phones_do_not_depend_on_the_other_lines_or_the_threads():
    # Bugs: a thread's decoder carrying a line's state into the next, so that its phones depend on the lines decoded
    # before: the noise remover's estimate (kept for live audio unless ps_start_stream resets it) or the top-N Gaussians
    # each utterance starts its search from (ptm_mgau.c, unless patches/ptm_mgau-reset-topn.patch is applied; unpatched,
    # voice 6 heard after voice 0 differs); or decoders sharing state across threads. Either makes the mouths differ
    # with the discs a player has, or between machines.
    model = Path(os.environ["WOZMOD_SPEECH"])
    speech = {0: _voice(0), 6: _voice(6)}
    texts = {0: "one two three", 6: "hello there"}
    for listen in ("heard", "aligned"):
        def run(lines, threads, listen=listen):
            given = {v: speech[v] if listen == "heard" else (speech[v], texts[v]) for v in lines}
            return getattr(Listener(model, threads), listen)(given)

        both = run([0, 6], 1)
        assert both[6] is not None
        assert run([6], 1) == {6: both[6]}
        assert run([0, 6], 2) == both


def test_a_speech_model_pocketsphinx_cannot_load_stops_the_build(tmp_path):
    # Bug: every line reported as not aligned and heard as nothing, so the install quietly makes the mouths from the
    # sound alone, as before this release, instead of saying its speech model is broken.
    with pytest.raises(ValueError, match="speech model"):
        Listener(tmp_path, 1).heard({1: bytes(3200)})
    with pytest.raises(OSError):  # the dictionary, read first to spell the words it lacks
        Listener(tmp_path, 1).aligned({1: (bytes(3200), "hello")})
