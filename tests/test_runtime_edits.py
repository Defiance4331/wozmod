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


@pytest.mark.parametrize(("rows", "count", "value_stop", "default"), [(1, 8, 5, 7), (2, 9, 6, 8)])
def test_settings_rows_does_not_leave_default_at_the_old_index(
    rows: int, count: int, value_stop: int, default: int,
) -> None:
    edits = runtime_edits.settings_rows(rows)
    assert [(va, len(stock), len(patched)) for va, stock, patched in edits] == [
        (0x56B2ED, 17, 17), (0x56B60B, 3, 3), (0x56B750, 3, 3),
        (0x56B7E4, 3, 3), (0x56B806, 3, 3), (0x56B86A, 19, 19), (0x56B8AB, 19, 19),
    ]
    assert [edits[index][2][position] for index, position in [(0, 1), (1, 2), (2, 2), (3, 2), (4, 2)]] == [
        count, value_stop, value_stop, default, default,
    ]


def test_fixed_edit_port_does_not_change_a_baseline_patch_byte() -> None:
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
        "CODE_EDITS": baseline["CODE_EDITS"], "FONT_ROUTE": baseline["FONT_ROUTE"],
        "settings_rows(1)": baseline["settings_rows"](1), "settings_rows(2)": baseline["settings_rows"](2),
    }
    actual = {
        "CODE_EDITS": runtime_edits.CODE_EDITS, "FONT_ROUTE": runtime_edits.FONT_ROUTE,
        "settings_rows(1)": runtime_edits.settings_rows(1), "settings_rows(2)": runtime_edits.settings_rows(2),
    }
    assert actual == expected
