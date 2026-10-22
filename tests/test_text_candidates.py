"""tools/text_candidates.py: how a donor candidate stands against the table."""

from __future__ import annotations

from text_candidates import REVIEW, review_status

REF = ["wo2-psp", "fns", 270, "dialog", 3]


def test_review_status_hides_settled_rows_and_never_lets_psp_outrank_an_xbox_row() -> None:
    # Bug: the review list hides a stock line with no row, or our own row where official English exists; or it offers
    # a PSP candidate over an Xbox 360 row (text priority is WO1 X360, then WO2 X360, then WO2 PSP); or it floods the
    # list with rows that already say the donor's English.
    cases = [  # (row, candidate pointer, donor English, row's English, donor) -> status, needs review
        ((None, REF, "Charge!", None, "wo2-psp"), ("new", True)),
        (({"source": "wo2-psp", "ref": REF}, REF, "Charge!", "Charge!", "wo2-psp"), ("same", False)),
        (({"source": "wo2-psp", "ref": [*REF[:4], 2]}, REF, "Charge!", "Charge!", "wo2-psp"), ("same", False)),
        (({"source": "wo2-psp", "ref": [*REF[:4], 2]}, REF, "Charge!", "Retreat!", "wo2-psp"), ("differs", True)),
        (({"source": "authored", "en": "Go!"}, REF, "Charge!", "Go!", "wo2-psp"), ("ours", True)),
        (({"source": "edited", "ref": REF}, [*REF[:4], 4], "Charge!", "Go!", "wo2-psp"), ("ours", True)),
        (({"source": "wo2-x360", "ref": ["wo2-x360", "ens", 143, "dialog", 0]}, REF, "Charge!", "Go!", "wo2-psp"),
         ("xbox", False)),
        (({"source": "wo1-x360", "ref": ["wo1-x360", "ens", 96, "dialog", 1]}, REF, "Charge!", "Go!", "wo2-psp"),
         ("xbox", False)),
        (({"source": "wo2-psp", "ref": REF}, None, "Charge!", "Go!", "wo2-x360"), ("xbox-unused", True)),
        (({"source": "wo1-x360", "ref": ["wo1-x360", "ens", 96, "dialog", 1]}, None, "Charge!", "Go!", "wo2-x360"),
         ("differs", True)),
    ]

    wrong = [(case, expected, (status := review_status(*case), status in REVIEW))
             for case, expected in cases if (review_status(*case), review_status(*case) in REVIEW) != expected]
    assert wrong == []
