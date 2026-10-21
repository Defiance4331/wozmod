"""Synthetic executable guards for runtime edits and hooks."""

from __future__ import annotations

import pytest

from synthetic import pe_image
from wozmod.runtime.english_runtime import (
    ENGLISH,
    ONE_ROW,
    TEST_HOOKS,
    TWO_ROWS,
    RuntimeBuildError,
    check_spans,
    hook_sites,
)


SECTION_VA = 0x79B000
RAW = 0x200
EDIT = SECTION_VA + 0x10
ROW = SECTION_VA + 0x20
ENGLISH_HOOK = SECTION_VA + 0x30
ALWAYS_HOOK = SECTION_VA + 0x40
WIDTH = 0x79BAC0


def stock_image() -> bytes:
    image = pe_image(RAW + 0x1000, (b".fixture", 0x1000, 0x9B000, 0x1000, RAW), image_base=0x700000)
    for va, value in [(EDIT, b"abc"), (ROW, b"de"),
                      (ENGLISH_HOOK, b"12345"), (ALWAYS_HOOK, b"ABCDE")]:
        at = RAW + va - SECTION_VA
        image[at:at + len(value)] = value
    return bytes(image)


def spans() -> tuple[dict, list]:
    edits = {
        ENGLISH: [(EDIT, b"abc", b"xyz")],
        ONE_ROW: [(ROW, b"de", b"fg")],
        TWO_ROWS: [(ROW, b"de", b"hi")],
    }
    hooks = [(ENGLISH_HOOK, b"12345", 7, None, True),
             (ALWAYS_HOOK, b"ABCDE", 8, 0, False)]
    return edits, hooks


@pytest.mark.parametrize("va", [EDIT, ROW, ENGLISH_HOOK, ALWAYS_HOOK, WIDTH],
                         ids=["edit", "row", "english-hook", "always-hook", "width"])
def test_changed_stock_byte_refuses_the_edit_hook_or_width_pointer(va: int) -> None:
    image = bytearray(stock_image())
    image[RAW + va - SECTION_VA] ^= 1
    edits, hooks = spans()
    with pytest.raises(RuntimeBuildError):
        check_spans(bytes(image), edits, hooks)


def test_different_length_edit_cannot_shift_stock_code() -> None:
    edits, hooks = spans()
    edits[ENGLISH] = [(EDIT, b"abc", b"xy")]
    with pytest.raises(RuntimeBuildError, match="changes its length"):
        check_spans(stock_image(), edits, hooks)


def test_four_byte_hook_cannot_hold_a_five_byte_jump() -> None:
    edits, hooks = spans()
    hooks[0] = (ENGLISH_HOOK, b"1234", 7, None, True)
    with pytest.raises(RuntimeBuildError, match="shorter than a jump"):
        check_spans(stock_image(), edits, hooks)


@pytest.mark.parametrize("group, target", [(ENGLISH, EDIT), (ONE_ROW, EDIT),
                                          (TWO_ROWS, ENGLISH_HOOK), (ONE_ROW, ALWAYS_HOOK),
                                          (TWO_ROWS, WIDTH)],
                         ids=["edits", "one-row-edit", "two-row-hook", "always-hook", "width"])
def test_overlapping_coinstalled_spans_are_refused(group: int, target: int) -> None:
    image = stock_image()
    edits, hooks = spans()
    at = RAW + target - SECTION_VA
    before = image[at:at + 1]
    edits[group].append((target, before, b"!"))
    with pytest.raises(RuntimeBuildError, match="spans overlap"):
        check_spans(image, edits, hooks)


def test_adjacent_edits_and_mutually_exclusive_rows_do_not_false_overlap() -> None:
    edits, hooks = spans()
    edits[ONE_ROW].append((EDIT + 3, b"\0", b"!"))
    check_spans(stock_image(), edits, hooks)


@pytest.mark.parametrize("defines, test_enabled", [((), False), (("ENRT_LOG",), False),
                                                  (("ENRT_TEST",), True),
                                                  (("ENRT_TEST", "ENRT_LOG"), True)])
def test_test_only_hook_does_not_leak_into_release_or_log_builds(
    defines: tuple[str, ...], test_enabled: bool,
) -> None:
    hooks = hook_sites(defines)
    ids = [hook[2] for hook in hooks]
    assert len(ids) == len(set(ids))
    assert {hook[2] for hook in TEST_HOOKS} <= set(ids) if test_enabled else set(ids).isdisjoint(hook[2] for hook in TEST_HOOKS)
    assert next(h for h in hooks if h[2] == 1) == (
        0x54B7D0, bytes.fromhex("83ec348b442448"), 1, 0, True,
    )
    assert next(h for h in hooks if h[2] == 24) == (
        0x5C9FD0, bytes.fromhex("83ec1c5355"), 24, None, False,
    )
