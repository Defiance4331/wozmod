"""Advances from the shape of each letter's sides (wozmod.fonts.side_spacing), on synthetic outlines."""

from __future__ import annotations

from wozmod.fonts import side_spacing as ss

BODY = (0, 600)
CAP = 120


def test_a_straight_side_gets_more_room_than_a_round_one_and_a_round_more_than_a_diagonal():
    # Catches room set the same for every side (round and diagonal letters then read loose beside straight ones)
    # or the profile read from the wrong side.
    straight = [[(0, 0), (100, 0), (100, 600), (0, 600)]]                       # a stem: flush both sides
    round_ = [[(100, 0), (160, 30), (200, 300), (160, 570), (100, 600), (40, 570), (0, 300), (40, 30)]]
    diagonal = [[(0, 600), (60, 600), (300, 0), (240, 0)]]                      # leans: open low left, high right
    left = {name: ss.side_depths(c, BODY, CAP)[0] for name, c in
            (("straight", straight), ("round", round_), ("diagonal", diagonal))}
    assert left["straight"] < left["round"] < left["diagonal"]
    room = 110
    advance, start = ss.place(straight, BODY, CAP, room)
    assert advance == round((100 + 2 * room) / 50) and abs(start - (room + (advance * 50 - 100 - 2 * room) / 2)) < 1e-6


def test_the_calibrated_room_keeps_the_target_mean_width():
    # Catches a calibration that drifts from the target, which would widen or narrow every line of text.
    glyphs = {"n": ([[(0, 0), (500, 0), (500, 600), (0, 600)]], BODY),
              "o": ([[(250, 0), (500, 300), (250, 600), (0, 300)]], BODY)}
    weights = {"n": 3.0, "o": 1.0}
    room = ss.calibrate(glyphs, weights, 14.0, CAP)
    mean = 0.0
    for ch, w in weights.items():
        contours, body = glyphs[ch]
        dl, dr = ss.side_depths(contours, body, CAP)
        mean += w * (500 + max(0, room - dl) + max(0, room - dr)) / 50
    assert abs(mean / 4.0 - 14.0) < 1e-3
