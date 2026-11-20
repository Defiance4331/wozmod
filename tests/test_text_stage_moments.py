"""Stage lines pair by moment (decisions ledger text row 49): synthetic stage scripts through stage_join, and WO2
X360's Dream team table against MOZ's copy of it."""

import os
from pathlib import Path

import pytest

from wozmod.formats.scripts import Line, Script, Table
from wozmod.text.script_alignment import stage_join

AND, RAN, TEAM, KILLED = 0xFF, 0x11, 0x23, 0x30  # KILLED: any test the matching compares as a plain leaf


def _words(*words: int) -> tuple[int, ...]:
    return (*words, *(0,) * (8 - len(words)))


def _leaf(kind: int, *words: int) -> tuple:
    return ((kind, _words(*words)),)


def _all(*children: tuple) -> tuple:
    """All of children: the group condition, then each child's conditions in prefix order."""
    return ((AND, _words(len(children))),) + tuple(c for child in children for c in child)


def _team(officer: int, on: bool) -> tuple:
    return _leaf(TEAM, 0, officer, int(on))


def _ran(event: int) -> tuple:
    return _leaf(RAN, event, 1, 0)


def _speak(person: int, line: int) -> tuple:
    return (0x26, person, line, 0, 0, 0, 0, 0)


def _stage(lines: int, *events: tuple[tuple, tuple]) -> Script:
    """A stage script of lines spoken lines and events given as (conditions, commands)."""
    return Script(Table(()), Table(tuple(Line(b"00Line %d@%04d" % (i, i + 1), True) for i in range(lines))),
                  events=tuple(commands for _, commands in events),
                  conditions=tuple(conditions for conditions, _ in events))


def _join(stock: Script, donor: Script, team: frozenset[int] | None = None) -> dict[int, int]:
    return {r: d for (_, r), d in stage_join(stock, donor, {p: p for p in range(100)}, team).items()}


A, B = _leaf(KILLED, 1), _leaf(KILLED, 2)


def test_a_line_pairs_with_the_donor_line_its_own_moment_speaks_whatever_the_order():
    # Bug: lines paired by their order (in the line table or across the command streams), so two scenes MOZ holds in
    # the other order cross: Dream stage 48's messenger success lines played the failure lines' takes and the other
    # way round. Here the donor speaks its lines 0 and 1 on trigger A and 2 and 3 on trigger B; MOZ holds the B scene
    # first. The two scenes' commands are alike, so only the trigger tells them apart. Expected by hand.
    donor = _stage(4, (A, (_speak(7, 0), _speak(8, 1))), (B, (_speak(7, 2), _speak(8, 3))))
    stock = _stage(4, (B, (_speak(7, 0), _speak(8, 1))), (A, (_speak(7, 2), _speak(8, 3))))
    assert _join(stock, donor) == {0: 2, 1: 3, 2: 0, 3: 1}


def test_a_team_variant_takes_no_donor_line_under_the_donor_team():
    # Bug: the team gates are left as plain tests, so the event that runs for teams without officer 44 (MOZ's variant,
    # like Dream stage 60 line 54) and the one that runs with officer 44 (WO2's team, like 60.27) score alike, neither
    # pairs, and the line table hands the donor line to the variant's line, which comes first. Expected by hand:
    # under the team {44} the variant cannot run; its line keeps the Japanese.
    donor = _stage(1, (A, (_speak(7, 0),)))
    stock = _stage(2, (_all(_team(44, False), A), (_speak(7, 0),)), (_all(_team(44, True), A), (_speak(7, 1),)))
    assert _join(stock, donor, frozenset({44})) == {1: 0}


def test_a_line_spoken_at_a_matched_moment_keeps_its_pair_where_a_variant_speaks_it_too():
    # Bug: a line also spoken at a moment that matches nothing (a team variant) loses the English its matched moment
    # gives it, though one recording plays at both (33 Dream lines, Dream 50.24 among them). The variant gives the
    # line to another speaker and the donor holds a line no command speaks, so the line table cannot pair it either.
    # Expected by hand.
    donor = _stage(2, (A, (_speak(7, 0),)))
    stock = _stage(1, (_all(_team(44, True), A), (_speak(7, 0),)), (_all(_team(44, False), B), (_speak(9, 0),)))
    assert _join(stock, donor, frozenset({44})) == {0: 0}


def test_every_piece_of_an_event_moz_split_pairs_with_its_part_of_the_donor_event():
    # Bug: a chain piece left unpaired: MOZ splits one donor event (persons 7 then 8 on trigger A) into a head on the
    # trigger and a piece gated on the head having run (Dream stage 48, WO2 event 68 as MOZ 79 and 80), so neither
    # stock event alone has the donor's speakers; MOZ's line table holds the two lines the other way round, so the
    # table pairs at most one of them. Expected by hand.
    donor = _stage(2, (A, (_speak(7, 0), _speak(8, 1))))
    stock = _stage(2, (A, (_speak(7, 1),)), (_ran(0), (_speak(8, 0),)))
    assert _join(stock, donor) == {1: 0, 0: 1}


def test_identical_twin_events_pair_in_their_order():
    # Bug: two events with one trigger and the same commands that speak different lines (story stage 8, WO2 events
    # 28 and 33, which MOZ copies) tie, pair nothing, and the line table pairs them by line order, which MOZ's table
    # holds the other way round. Expected by hand: the first twin with the first, the second with the second.
    donor = _stage(2, (A, (_speak(7, 0),)), (A, (_speak(7, 1),)))
    stock = _stage(2, (A, (_speak(7, 1),)), (A, (_speak(7, 0),)))
    assert _join(stock, donor) == {1: 0, 0: 1}


def test_a_line_its_moments_put_on_two_donor_lines_takes_its_table_pair():
    # Bug: a line spoken at two moments whose donor events speak different lines (Dream stage 50 line 5: four moments
    # on WO2 line 5, one on its line 8) keeps the Japanese, or takes whichever moment came last. Expected by hand:
    # the table pairs stock line 0 with donor line 0.
    donor = _stage(2, (A, (_speak(7, 0),)), (B, (_speak(7, 1),)))
    stock = _stage(1, (A, (_speak(7, 0),)), (B, (_speak(7, 0),)))
    assert _join(stock, donor) == {0: 0}


@pytest.mark.skipif(not (os.environ.get("WOZMOD_GAME") and os.environ.get("WOZMOD_DONORS")),
                    reason="set WOZMOD_GAME and WOZMOD_DONORS")
def test_wo2_dream_teams_are_the_officers_moz_keeps_for_each_dream_stage():
    # Bugs: the table read at a shifted offset or row stride, or a row given to the next stage, so every Dream stage
    # pairs under another stage's team. Oracle: MOZ's own copy of the rows (OROCHI_Z.exe VA 0x6D62F2, 6 bytes a row
    # from stage 40, bytes 0-2 the officers, read by 0x476E00), a second file the reader never touches.
    from wozmod import game_files
    from wozmod.donors import DonorsFolder
    from wozmod.formats import pe
    from wozmod.text.script_alignment import dream_teams

    exe = game_files.exe(Path(os.environ["WOZMOD_GAME"]))
    at = pe.file_offset(pe.layout(exe), 0x6D62F2)
    moz = {40 + k: frozenset(exe[at + 6 * k:at + 6 * k + 3]) for k in range(28)}
    assert dream_teams(DonorsFolder(Path(os.environ["WOZMOD_DONORS"])).program("wo2-x360")) == moz
