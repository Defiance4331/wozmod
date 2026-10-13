"""Behavioral checks for fixed runtime edit assembly."""

from __future__ import annotations

import os
import subprocess

import pytest

from wozmod.runtime import runtime_edits


def test_assembled_calls_use_each_instruction_end_for_rel32() -> None:
    assert runtime_edits.assembled(0x1000, b"\x90\x90", 0x2000, b"\x90", 0x0800) == bytes.fromhex(
        "9090 e8f90f0000 90 e8f3f7ffff"
    )


def test_detour_does_not_pad_past_the_overwritten_stock_span() -> None:
    assert runtime_edits.detour(0x1000, b"\x90" * 7, 0x2000, 0x1007, b"\x50", 0x3000) == [
        (0x1000, b"\x90" * 7, bytes.fromhex("e9fb0f0000 9090")),
        (0x2000, b"\xcc" * 11, bytes.fromhex("50 e8fa0f0000 e9fcefffff")),
    ]


@pytest.mark.parametrize(("rows", "distance", "count", "value_stop", "default"),
                         [(1, True, 9, 6, 8), (2, True, 10, 7, 9), (1, False, 8, 5, 7), (2, False, 9, 6, 8)])
def test_settings_rows_does_not_leave_default_at_the_old_index(
    rows: int, distance: bool, count: int, value_stop: int, default: int,
) -> None:
    # Bug: a release build keeps a cursor slot for the compiled-out render distance row, an empty row.
    edits = runtime_edits.settings_rows(rows, distance)
    assert [(va, len(stock), len(patched)) for va, stock, patched in edits] == [
        (0x56B2ED, 17, 17), (0x56B60B, 3, 3), (0x56B750, 3, 3),
        (0x56B7E4, 3, 3), (0x56B806, 3, 3), (0x56B86A, 19, 19), (0x56B8AB, 19, 19),
    ]
    assert [edits[index][2][position] for index, position in [(0, 1), (1, 2), (2, 2), (3, 2), (4, 2)]] == [
        count, value_stop, value_stop, default, default,
    ]


LIVE_TEXT_GROWTH_REMOVED = {
    0x526DE4, 0x52742C, 0x527464, 0x527705, 0x527719, 0x527722, 0x52776D,
    0x5277C7, 0x528F19, 0x528F27, 0x5290EA, 0x5290F8, 0x52913C, 0x529150,
    0x5291CC, 0x5291DF, 0x5291EB, 0x5291F4, 0x529246, 0x529270, 0x5292F1,
    0x529587, 0x52959B, 0x5295B0, 0x5295DC, 0x52B59B, 0x52B6B9, 0x52B715,
    0x52B75C, 0x52B817, 0x52B8EA, 0x52B956, 0x52B9C3, 0x52BA67, 0x52BAB7,
    0x52BACB, 0x52BADF, 0x52BB7D, 0x52BB9C, 0x52BBFF, 0x52BC2F, 0x52CCF2,
    0x52CD02, 0x52D072, 0x52D08C, 0x52D3CF, 0x52D663, 0x52D757, 0x52D76B,
    0x52D77E, 0x52D7A4, 0x52E7B2, 0x52E83B, 0x52EA74, 0x52EB02, 0x52EBAB,
    0x52EC31, 0x52ED51, 0x52EEE0, 0x52FA62, 0x52FC4D, 0x52FC55, 0x52FD1E,
    0x5332F8, 0x53338F, 0x53343A, 0x533756, 0x533768, 0x535E5D, 0x536654,
    0x53665C, 0x536694, 0x5366FD, 0x536791, 0x536828, 0x5368C4, 0x5368F3,
    0x536970, 0x536984, 0x53699B, 0x538C9C, 0x538CA3, 0x538CB0, 0x539F94,
    0x53A625, 0x53A631, 0x53A663, 0x53A68B, 0x53A73D, 0x53A7A0, 0x53A7AC,
    0x5432F9, 0x5488E8, 0x5488F8, 0x548900, 0x548910, 0x552E76,
    0x552F09, 0x552F7F, 0x553139, 0x55315E, 0x5531BB, 0x5531DA, 0x554387,
    0x556D81, 0x556E7A, 0x55742F, 0x557443, 0x557456, 0x557506, 0x557529,
    0x55B55E, 0x55B656, 0x55BAF0, 0x55BB04, 0x55BB15, 0x55BBE7, 0x55BC0A,
    0x56108C, 0x561098, 0x56A7A1, 0x56A7B5, 0x56A7D0, 0x58076A, 0x580780,
    0x580791, 0x594EDA, 0x59506C, 0x595135, 0x5951E6, 0x5954A3, 0x595541,
    0x5955B6, 0x595AC1, 0x595BB6, 0x5982D3, 0x598395, 0x59840D, 0x598495,
    0x5984DD, 0x5984E5, 0x598534, 0x59853F, 0x59858A, 0x59863F, 0x59866D,
    0x5986D4, 0x5986F4, 0x598859, 0x598DFC, 0x598E08, 0x598E1B, 0x598E38,
    0x598ED1, 0x598F01, 0x59C71C, 0x59C728, 0x5A66F3, 0x5A6700, 0x5A6783,
    0x5F52C0, 0x5F5300, 0x5F5340, 0x5F5380, 0x5F53C0, 0x5F5400, 0x5F5440,
    0x5F55E0, 0x5F5680, 0x5F56A0, 0x5F56C0, 0x5F56E0, 0x5F5700,
}

ART_AND_OFFICIAL_GEOMETRY = {
    0x553885: "6867010000", 0x582619: "81c6f0010000", 0x595F17: "6838020000",
    0x596839: "68d6030000", 0x596845: "6894000000", 0x596854: "6894000000",
    0x596865: "6894000000", 0x596877: "6894000000", 0x596890: "8d510a",
    0x5968A1: "8d480a", 0x597517: "68e9020000", 0x5975CF: "68ae020000",
    0x59761D: "68ae020000",
    0x5F5640: "6880000000689600000068d8010000e9cafff9ff",
    0x5F5660: "6880000000689600000068d8010000e93600faff",
    0x5F5840: "6a1c6896000000056c010000e94474f3ff",
}

# Kept vertical growth for page and history text, now the X360 panel, band and history geometry.
REPLACED_BY_X360 = {0x548976, 0x548980, 0x548A0E, 0x561087, 0x53A620, 0x53A62C, 0x53A655, 0x53A65E,
                    0x53A686, 0x53A699, 0x53A764, 0x53A79E, 0x53A7A7, 0x710298}
# Kept faction-label, grade-strip and still-plate art edits, now the X360 panels and art.
REPLACED_BY_X360_PANELS = {
    0x595ADE, 0x595AEF, 0x595AF2, 0x595B0F, 0x527821, 0x527829, 0x527832, 0x529655, 0x52965D, 0x529666,
    0x52D819, 0x52D821, 0x52D82A, 0x56A848, 0x56A850, 0x56A859, 0x556D9F, 0x556DB4, 0x556DB7, 0x556DD5,
    0x55B57B, 0x55B58C, 0x55B58F, 0x55B5AC, 0x5574DF, 0x5574E7, 0x5574F0, 0x55BB9A, 0x55BBA0, 0x55BBA8,
    0x52B5B7, 0x52B5DA, 0x52B5E1, 0x52B5F2, 0x52BB4A, 0x52BB52, 0x52BB5B, 0x533315, 0x53332F, 0x533346,
    0x533349, 0x533365, 0x53336D, 0x533372, 0x59C3DF, 0x59C3E6, 0x59C421, 0x59C42C, 0x59C48E,
}


def test_wave4_does_not_restore_live_text_growth_or_change_other_baseline_bytes() -> None:
    old_repo = os.environ.get("WOZMOD_OLD_REPO")
    if not old_repo:
        pytest.skip("set WOZMOD_OLD_REPO for the migration baseline")
    source = subprocess.run(
        ["git", "-C", old_repo, "show", "2fa29d5:src/musou_patch/targets/musou_orochi_z/runtime_edits.py"],
        capture_output=True, check=True, text=True,
    ).stdout
    baseline: dict = {}
    exec(compile(source, "baseline-runtime-edits", "exec"), baseline)
    expected = {
        "CODE_EDITS": [
            (va, stock, bytes.fromhex(ART_AND_OFFICIAL_GEOMETRY[va]) if va in ART_AND_OFFICIAL_GEOMETRY else patched)
            for va, stock, patched in baseline["CODE_EDITS"]
            if va not in LIVE_TEXT_GROWTH_REMOVED | REPLACED_BY_X360 | REPLACED_BY_X360_PANELS
        ] + runtime_edits.MOZ_LAYOUT + runtime_edits.X360_LAYOUT + runtime_edits.X360_PANELS
          + runtime_edits.LAYOUT_TWEAKS + runtime_edits.PAGE_ORIGIN + runtime_edits.CAPTION_LINES,
        "FONT_ROUTE": baseline["FONT_ROUTE"],
        "settings_rows(1)": baseline["settings_rows"](1), "settings_rows(2)": baseline["settings_rows"](2),
    }
    actual = {
        "CODE_EDITS": runtime_edits.CODE_EDITS, "FONT_ROUTE": runtime_edits.FONT_ROUTE,
        "settings_rows(1)": runtime_edits.settings_rows(1, True), "settings_rows(2)": runtime_edits.settings_rows(2, True),
    }
    assert actual == expected
    # The screens that keep the MOZ layout get the baseline bytes back, except the gallery list name y
    # (centred in the X360 plate), which the baseline never moved.
    old = {va: (va, stock, patched) for va, stock, patched in baseline["CODE_EDITS"]}
    restored = [edit for edit in runtime_edits.MOZ_LAYOUT if edit[0] != 0x55B624]
    assert restored == [old[edit[0]] for edit in restored]
