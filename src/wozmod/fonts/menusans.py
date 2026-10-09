"""Menusans rendering support."""
import importlib.util
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import fontforge
import numpy as np

from .skeleton import Skeleton, merge
from .menu_stroke import pinched_stroke

CAP = 17.2
BASE = 19.3
U = 1000
OVERSHOOT = 0.3
ALIASES = {}
REDRAWN = "BDPRSsabdefghjmnpqrtuyŌōū()."
JOINTS = "RZz"


DRAWING_WIDTH = {"A": 1.015, "D": 1.05, "H": 1.05, "K": 1.04,
                 "R": 1.04, "X": 1.09675, "Y": 1.09,
                 "Z": 1.045, "c": 1.035, "v": 1.025, "y": 1.04}


SIDEBEARINGS = {
    '(': (3.25, 1.5),
    ')': (1.23, 1.54),
    '-': (0.65, 0.92),
    '.': (0.69, 1.31),
    'A': (0.31, 0.23),
    'B': (1.64, 0.67),
    'C': (1.43, 0.88),
    'D': (1.61, 1.04),
    'E': (1.46, 0.56),
    'F': (1.93, 0.91),
    'G': (1.47, 1.27),
    'H': (1.53, 1.99),
    'I': (1.71, 1.71),
    'J': (1.34, 2.49),
    'K': (1.76, 0.74),
    'L': (1.69, 0.36),
    'M': (1.7, 1.76),
    'N': (1.53, 1.64),
    'O': (0.93, 1.21),
    'P': (1.57, 1.18),
    'Q': (0.9, 1.23),
    'R': (1.52, 0.55),
    'S': (0.84, 1.06),
    'T': (-0.18, 0.47),
    'U': (1.33, 1.4),
    'V': (-0.16, 0.12),
    'W': (0.51, 0.46),
    'X': (0.85, 1.22),
    'Y': (-0.02, 0.87),
    'Z': (1.44, 1.53),
    'a': (1.45, 1.7),
    'b': (1.92, 0.77),
    'c': (1.02, 0.81),
    'd': (1.49, 2.01),
    'e': (1.2, 1.58),
    'f': (-0.14, 1.17),
    'g': (0.91, 1.47),
    'h': (2.15, 1.88),
    'i': (2.52, 2.16),
    'j': (1.92, 2.7),
    'k': (2.59, 1.39),
    'l': (1.44, 1.44),
    'm': (1.78, 1.81),
    'n': (2.13, 2.12),
    'o': (1.22, 1.34),
    'p': (1.42, 1.0),
    'q': (0.68, 0.87),
    'r': (1.73, 0.43),
    's': (0.76, 1.18),
    't': (0.27, 0.77),
    'u': (2.0, 2.27),
    'v': (0.12, 0.84),
    'w': (0.47, 0.49),
    'y': (0.49, 0.68),
    'z': (2.12, 2.27),
    'Ō': (1.51, 2.6),
    'ō': (2.16, 2.83),
    'ū': (2.95, 3.25),
}
KERNING = {
    'Ch': 0.5,
    'FA': -2.1,
    'Fe': 1.4,
    'Ku': 1.48,
    'NC': -0.77,
    'PA': -2.02,
    'TA': -1.55,
    'TY': 1.49,
    'Yi': 0.35,
    'ct': 0.65,
    'fi': -2.5,
    'ga': -0.67,
    'hi': -0.3,
}


def spacing(ch: str, box: tuple[float, float, float, float]) -> tuple[float, float]:


    if ch in "0123456789":
        return box[0] + (2.45 if ch == "1" else 0), 18.0
    left, right = SIDEBEARINGS.get(ch, (1.0, 1.0))
    return left, box[2] - box[0] + left + right


@dataclass(frozen=True)
class Axes:


    pen: tuple[float, float] = (0.95, 0.95)
    w_width: float = 1.34
    lower_w_width: float = 1.14
    m_width: float = 1.10
    x_height: float = 1.0
    descender: float = 1.0
    corner_radius: float = 1.0
    slant: float = 0.0
    pinch: tuple[float, float] = (0.5, 2.5)
    construction: str = "squared"
    g_form: str = "double"
    g_hook: str = "legacy"
    g_pen: tuple[float, float] = (1.0, 1.0)
    aligned_joins: bool = False
    flush_terminals: bool = False

    def __post_init__(self):
        values = (*self.pen, self.w_width, self.lower_w_width, self.m_width, self.x_height,
                  self.descender, self.corner_radius, self.slant)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("menu face axes must be finite")
        if len(self.pen) != 2 or any(v <= 0 for v in values[:-1]):
            raise ValueError("menu face dimensions and pen gains must be positive")
        if len(self.pinch) != 2 or not all(math.isfinite(v) for v in self.pinch):
            raise ValueError("menu pinch depth and length must be finite")
        if self.pinch[0] < 0 or self.pinch[1] <= 0:
            raise ValueError("menu pinch depth must be nonnegative and length positive")
        if self.construction not in {"squared", "round"}:
            raise ValueError("unknown menu skeleton construction")
        if self.g_form not in {"single", "double"}:
            raise ValueError("unknown menu g construction")
        if len(self.g_pen) != 2 or not all(math.isfinite(v) and v > 0 for v in self.g_pen):
            raise ValueError("menu g pen gains must be finite and positive")
        if self.g_hook not in {"legacy", "symmetric", "foot"}:
            raise ValueError("unknown menu g hook construction")
        if not isinstance(self.aligned_joins, bool):
            raise ValueError("menu join alignment must be boolean")
        if not isinstance(self.flush_terminals, bool):
            raise ValueError("menu terminal alignment must be boolean")


class _Skeleton(Skeleton):
    def __init__(self, axes):
        super().__init__(BASE, (2.6, 2.2), dk=0.93, u=U)
        self.axes = axes
        self.character = ""
        self.join_sites = []

    def fp(self, x, y):
        factor = (self.axes.w_width if self.character == "W" else
                  self.axes.lower_w_width if self.character == "w" else
                  self.axes.m_width if self.character in "Mm" else 1.0)
        factor *= DRAWING_WIDTH.get(self.character, 1.0)
        x *= factor
        if self.character.islower() and self.character not in "gjy":


            if y >= BASE:
                if self.character in "pq":
                    y = BASE + (y - BASE) * self.axes.descender
            elif y >= 6.2:
                y = BASE - (BASE - y) * self.axes.x_height
            elif y > 2.1:
                top = BASE - (BASE - 6.2) * self.axes.x_height
                y = 2.1 + (y - 2.1) * (top - 2.1) / (6.2 - 2.1)
        return (x * U, (BASE - y) * U)

    def contour(self, pts, radii, closed):


        rs = list(radii) if isinstance(radii, (tuple, list)) else [radii] * len(pts)
        rs = [r * self.axes.corner_radius for r in rs]
        factors = []
        for i in range(len(pts)):
            if not closed and i in (0, len(pts) - 1):
                rs[i] = 0
                factors.append(0)
                continue
            a, v, b = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            la, lb = math.dist(a, v), math.dist(b, v)
            dot = sum((a[k] - v[k]) * (b[k] - v[k]) for k in (0, 1)) / (la * lb)
            angle = math.acos(max(-1.0, min(1.0, dot)))
            factors.append(1 / math.tan(angle / 2) if angle > 1e-3 else 0)


        for i in range(len(pts) if closed else len(pts) - 1):
            j = (i + 1) % len(pts)
            distance = rs[i] * factors[i] + rs[j] * factors[j]
            limit = math.dist(pts[i], pts[j]) * 0.998
            if distance > limit:
                rs[i] *= limit / distance
                rs[j] *= limit / distance
        return super().contour(pts, rs, closed)

    def stroke(self, pts, r=0, closed=False, pen=None, box=None):
        p = pen or self.pen
        return super().stroke(pts, r, closed,
                              (p[0] * self.axes.pen[0], p[1] * self.axes.pen[1], *p[2:]), box)

    def joined_stroke(self, pts, r=0, closed=False, pen=None, box=None,
                      joins=(), kind="bowl-to-stem", waists=()):


        p = pen or self.pen
        actual_pen = (p[0] * self.axes.pen[0], p[1] * self.axes.pen[1], *p[2:])
        depth, recovery = self.axes.pinch
        for point in joins:
            x, y = self.fp(*point)
            self.join_sites.append({"ch": self.character,
                                    "point": (x / U, CAP - y / U), "kind": kind})
        for point in waists:
            x, y = self.fp(*point)
            self.join_sites.append({"ch": self.character,
                                    "point": (x / U, CAP - y / U), "kind": "waist"})
        return pinched_stroke(self, pts, r, closed, actual_pen, waists,
                              depth, recovery, box=box)

    def curve(self, p0, *cps, pen=None, box=None, closed=False):
        p = pen or self.pen
        return super().curve(p0, *cps,
                             pen=(p[0] * self.axes.pen[0], p[1] * self.axes.pen[1], *p[2:]),
                             box=box, closed=closed)

    def fill(self, x0, y0, x1, y1):


        center = (x0 + x1) / 2
        half = (x1 - x0) * self.axes.pen[0] / 2
        if y1 - y0 <= 4:
            cy = (y0 + y1) / 2
            dy = (y1 - y0) * self.axes.pen[1] / 2
            y0, y1 = cy - dy, cy + dy
        return super().fill(center - half, y0, center + half, y1)

    def aimed(self, c1, c2, pen, box):


        p = (pen[0] * self.axes.pen[0], pen[1] * self.axes.pen[1], *pen[2:])
        plain = Skeleton(BASE, p, u=U)
        mapped = lambda q: (self.fp(*q)[0] / U, BASE - self.fp(*q)[1] / U)
        a, b = mapped(box[:2]), mapped(box[2:])
        return plain.aimed(mapped(c1), mapped(c2), p, (*a, *b))

    def kjoin(self, top, joint, bottom, stem_x, box, pen=None, leg_pen=None):
        p = pen or self.diag()
        q = leg_pen or p
        p = (p[0] * self.axes.pen[0], p[1] * self.axes.pen[1], *p[2:])
        q = (q[0] * self.axes.pen[0], q[1] * self.axes.pen[1], *q[2:])
        plain = Skeleton(BASE, p, u=U)
        mapped = lambda xy: (self.fp(*xy)[0] / U, BASE - self.fp(*xy)[1] / U)
        a, b = mapped(box[:2]), mapped(box[2:])
        stem = mapped((stem_x, BASE))[0]
        return plain.kjoin(mapped(top), mapped(joint), mapped(bottom), stem, (*a, *b), p, q)


def _draw_squared(ch, s, v):

    sv, sh = s.pen
    if ch == "B":
        return [s.vline(2.3, 2.1, BASE),
                s.joined_stroke([(2.3, v.BT), (14.4, v.BT), (14.4, 10.2), (2.3, 10.2)],
                                [0, 2.0, 4.0, 0],
                                waists=[(14.4, 10.2)]),
                s.joined_stroke([(2.3, 10.2), (15.0, 10.2), (15.0, v.BB), (2.3, v.BB)],
                                [0, 3.5, 2.4, 0],
                                waists=[(15.0, 10.2)])]
    if ch in "DP":
        x = 2.3 if ch == "D" else 2.25
        right = 15.6 if ch == "D" else 15.0
        bottom = v.BB if ch == "D" else 11.85
        radius = 4.5 if ch == "D" else 3.0
        return [s.vline(x, 2.1, BASE),
                s.joined_stroke([(x, v.BT), (right, v.BT), (right, bottom), (x, bottom)],
                                [0, radius, radius, 0])]
    if ch == "S":
        return [v.s_curve(2.35, 15.0, 15.7, v.CT - .15, v.CB - .05,
                          8.0, 14.0, 10.05, .131, v.RC - 1, 2.6)]
    if ch == "s":
        return [v.s_curve(2.325, 12.075, 12.525, v.LT - .025, v.LB + .025,
                          10.25, 15.5, 12.325, .14, 2.825, 2.125, r_bot=2.8)]
    if ch == "a":


        return [s.stroke([(2.95, 10.15), (2.95, v.LT), (12.8, v.LT),
                          (12.8, BASE + OVERSHOOT)], [0, 3.1, 2.5, 0]),
                s.stroke([(12.8, 12.4), (2.45, 12.4), (2.45, v.LB),
                                 (12.8, v.LB), (12.8, 15)], [0, 3.35, 2.75, 3.6, 0])]
    if ch == "b":
        return [s.vline(2.775, 2.1, BASE + OVERSHOOT),
                s.joined_stroke([(2.775, v.LT), (13.75, v.LT), (13.75, v.LB),
                                 (2.775, v.LB)], [4.025, 3.75, 3.75, 4.025], True,
                                joins=[(2.775 + 4.025 * s.axes.corner_radius, v.LT),
                                       (2.775 + 4.025 * s.axes.corner_radius, v.LB)])]
    if ch == "d":
        return [s.vline(13.25, 2.1, BASE + OVERSHOOT),
                s.joined_stroke([(2.25, v.LT), (13.25, v.LT), (13.25, v.LB), (2.25, v.LB)],
                                v.RL, True,
                                joins=[(13.25 - v.RL * s.axes.corner_radius, v.LT),
                                       (13.25 - v.RL * s.axes.corner_radius, v.LB)])]
    if ch == "e":
        return [s.stroke([(2.2, 12.1), (13.1, 12.1), (13.1, v.LT),
                                 (2.2, v.LT), (2.2, v.LB), (13.1, v.LB), (13.1, 14.4)],
                         [0, 0, v.RL, v.RL, v.RL, v.RL, 0])]
    if ch in "hnr":
        x = 2.25 if ch == "r" else 2.75


        radius = v.RL + .6 if ch == "r" else v.RL
        points = [(x, BASE if ch == "r" else 12), (x, v.LX),
                  (10.3 if ch == "r" else 12.75, v.LX)]
        radii = [0, radius, 0]
        if ch != "r":
            points.append((12.75, BASE))
            radii = [0, v.RL, v.RL, 0]
        return [s.vline(x, 2.1 if ch == "h" else 6.2, BASE),
                s.joined_stroke(points, radii,
                                joins=[(x + radius * s.axes.corner_radius, v.LX)],
                                kind="arch-to-stem")]
    if ch in "pq":
        stem = 2.75 if ch == "p" else 13.25
        left = 2.75 if ch == "p" else 2.25
        right = 13.75 if ch == "p" else 13.25
        bottom = 18.15 if ch == "p" else 18.0
        return [s.vline(stem, 7.0 - sh / 2, v.DS),
                s.joined_stroke([(left, 7.0), (right, 7.0), (right, bottom), (left, bottom)],
                                v.RL, True,
                                joins=[(stem + (1 if ch == "p" else -1) * v.RL * s.axes.corner_radius, 7.0),
                                       (stem + (1 if ch == "p" else -1) * v.RL * s.axes.corner_radius, bottom)])]
    if ch == "u":
        stem_bottom = 18.4 + sh / 2
        if s.axes.aligned_joins:


            rail = BASE - (BASE - 18.4) * s.axes.x_height + sh * s.axes.pen[1] / 2
            stem_bottom = rail if rail >= BASE else BASE - (BASE - rail) / s.axes.x_height
        return [s.joined_stroke([(2.75, 6.2), (2.75, 18.4), (12.75, 18.4), (12.75, 12)],
                                [0, v.RL, v.RL, 0],
                                joins=[(12.75 - v.RL * s.axes.corner_radius, 18.4)],
                                kind="arch-to-stem"),
                s.vline(12.75, 6.2, stem_bottom)]
    if ch == "f":
        top = 2.1 - OVERSHOOT + sh * s.axes.pen[1] / 2
        return [s.stroke([(5.75, BASE), (5.75, top), (11, top)], [0, 2.2, 0]),
                s.hline(8, 1.7, 11)]
    if ch == ".":
        lift = 3.15 * (1-s.axes.pen[1]) / 2
        return [s.fill(1.6, BASE-3.15+lift, 4.9, BASE+lift)]
    if ch == "t":
        return [s.stroke([(5.75, 2.82), (5.75, v.BB), (11.3, v.BB)], [0, 2.6, 0]),
                s.hline(7.2, 1.5, 11.3)]
    if ch == "Ō":
        bottom = BASE + 1.2 - sh * s.axes.pen[1] / 2
        return [s.rring(2.6, 5.1, 17.4, bottom, 4.2),
                s.hline(-.4, 6.3, 14, pen=(sv, v.MAC))]
    if ch in "ōū":
        body = v.G['o']() if ch == 'ō' else _draw_redrawn('u', s, v)
        return body + [
            s.hline(2.75, 3.7 if ch == 'ō' else 4, 11.5 if ch == 'ō' else 11.7,
                    pen=(sv, v.MAC))]
    if ch in "()":
        a, b, c = (6.2, 3.35, 2.2) if ch == '(' else (2.25, 5.1, 6.25)
        return [s.curve((a, -.8), (b, 3.35), (c, 6.6), (c, 10.5),
                        (c, 14.4), (b, 17.65), (a, 21.8), box=(-5, 0, 25, 21.6))]
    if ch == "R":


        leg_x = lambda y: 14.12 + 0.274 * (y - 13.5)
        extension = 1 / math.hypot(.274, 1)
        return [s.vline(2.77, 2.1, BASE),
                s.joined_stroke([(2.77, v.BT), (14.8, v.BT), (14.8, 11), (2.77, 11)],
                                [0, 2.4, 2.4, 0], waists=[(14.8, 11)]),


                (s.joined_stroke([(leg_x(11 - extension), 11 - extension),
                                  (leg_x(BASE + extension), BASE + extension)],
                                 waists=[(leg_x(11), 11)], pen=s.diag(),
                                 box=(-5, 11 - v.CSH * s.axes.pen[1] / 2,
                                      25, BASE)) if s.axes.pinch[0] else
                 s.line((leg_x(11), 11), (leg_x(BASE), BASE), ext=1,
                        box=(-5, 11, 25, BASE)))]
    if ch in JOINTS:
        bar = sh * s.axes.pen[1]
        offset = bar / (2 * s.axes.x_height) if ch == "z" else bar / 2
        top, bottom = (2.1 + bar / 2, BASE - bar / 2) if ch == "Z" else (7.0, 18.35)
        left_top, right_top, left_bottom, right_bottom = (
            (2.5, 16.0, 1.55, 16.47) if ch == "Z" else (2.0, 13.73, 1.53, 13.9))
        return [s.hline(top, left_top, right_top),
                s.hline(bottom, left_bottom, right_bottom),
                s.aimed((right_top, top + offset), (left_bottom, bottom - offset),
                        s.diag(), (left_bottom, top, right_top, bottom))]
    if ch == "m":

        middle = (2.85 + 17.9) / 2
        crown = (6.2 + sh * s.axes.pen[1] / (2 * s.axes.x_height)
                 if s.axes.aligned_joins else 7.15)
        return [s.vline(2.85, 6.2, BASE),
                s.joined_stroke([(2.85, 12), (2.85, crown), (middle, crown), (middle, BASE)],
                                [0, 2.4, 2.4, 0],
                                joins=[(2.85 + 2.4 * s.axes.corner_radius, crown)],
                                kind="arch-to-stem"),
                s.joined_stroke([(middle, 12), (middle, crown), (17.9, crown), (17.9, BASE)],
                                [0, 2.4, 2.4, 0],
                                joins=[(middle + 2.4 * s.axes.corner_radius, crown)],
                                kind="arch-to-stem")]
    body_top = BASE - (BASE - 6.2) * s.axes.x_height
    scaled_sh = sh * s.axes.pen[1]
    bottom = BASE + 3.7 * s.axes.descender - scaled_sh / 2
    if ch == "g":
        if s.axes.g_form == "single":
            return _draw_round(ch, s, v)


        upper_top = body_top - .4 - 1.25 * (1 - s.axes.descender) + scaled_sh / 2
        upper_bottom = BASE - 6.45 * s.axes.x_height
        lower_top = BASE - 2.95 * s.axes.x_height
        radius = min(2.75, (bottom - lower_top) / 2)
        return [s.rring(3.425, upper_top, 13.9, upper_bottom, 2.55),
                s.hline(upper_top, 13.9, 16.9),
                s.stroke([(7, upper_bottom), (3.7, upper_bottom), (3.7, lower_top),
                          (7, lower_top)], [0, 1.6, 1.6, 0]),
                s.rring(2.825, lower_top, 14.85, bottom, radius)]
    if ch == "j":
        return [s.stroke([(5.75, body_top), (5.75, bottom), (1.2, bottom)],
                         [0, 2.4 * s.axes.descender, 0]),
                s.fill(4.2, 2 - 1.6 * (1 - s.axes.descender),
                       7.3, 4.6 - 1.6 * (1 - s.axes.descender))]
    if ch == "y":


        slope = (14.68 - 7.03) / (21.625 - 4.0)
        fork_top = body_top - 1.2 * (1 - s.axes.descender)
        tail_bottom = BASE + 3.35 * s.axes.descender
        top = (14.68 - slope * (fork_top - 4.0), fork_top)
        tail = (top[0] - slope * (tail_bottom - fork_top), tail_bottom)
        length = math.dist(top, tail)
        normal = ((tail[1] - top[1]) / length, (top[0] - tail[0]) / length)
        foot_y = tail_bottom - s.diag()[1] * s.axes.pen[1] / 2
        corner = (top[0] - slope * (foot_y - fork_top), foot_y)
        start = (top[0] + slope * 2, fork_top - 2)
        foot = (corner[0] - 3.8, foot_y)
        return [s.cut(s.line((2.78, fork_top), (8.3, BASE - 1.3), ext=2.5,
                            box=(-5, fork_top, 25, tail_bottom)), top, normal, 0),
                s.stroke([start, corner, foot], [0, 2.0, 0], pen=s.diag(),
                         box=(-5, fork_top, 25, tail_bottom))]
    raise KeyError(ch)


def _outer_rail(layer, x, *, bottom=False):

    crossings = []
    for contour in _contours(layer):
        start = np.asarray(contour[0])
        for segment in contour[1:]:
            end = np.asarray(segment[-1])
            if segment[0] == "L":
                if abs(end[0] - start[0]) > 1e-12:
                    t = (x - start[0]) / (end[0] - start[0])
                    if 0 <= t < 1:
                        crossings.append(float(start[1] + t * (end[1] - start[1])))
            else:
                p = np.asarray([start, *segment[1:]])
                if p[:, 0].min() - 1e-9 <= x <= p[:, 0].max() + 1e-9:
                    coefficients = [-p[0, 0] + 3*p[1, 0] - 3*p[2, 0] + p[3, 0],
                                    3*p[0, 0] - 6*p[1, 0] + 3*p[2, 0],
                                    -3*p[0, 0] + 3*p[1, 0], p[0, 0] - x]
                    for root in np.roots(np.trim_zeros(coefficients, "f")):
                        if abs(root.imag) < 1e-8 and -1e-8 <= root.real < 1-1e-8:
                            t = max(0.0, float(root.real))
                            y = ((1-t)**3*p[0] + 3*(1-t)**2*t*p[1]
                                 + 3*(1-t)*t*t*p[2] + t**3*p[3])[1]
                            crossings.append(float(y))
            start = end
    if not crossings:
        raise ValueError("menu stem side does not intersect its branch")
    return (max if bottom else min)(crossings)


def _attribute_g_terminal(s, hook):


    tip_x = 2.25 - s.pen[0] * s.axes.pen[0] / 2 + .8558
    tip_y = BASE + .83 * s.axes.descender
    slope = .357474
    line_x, line_y = tip_x + 2.194353, tip_y + 2.194353 * slope
    inner_x, outer_x = 5.5, 2.45

    def rail(x, bottom=False):
        return _outer_rail(hook, x, bottom=bottom) + BASE - CAP

    def tangent(x, bottom=False):
        return (rail(x + 1e-4, bottom) - rail(x - 1e-4, bottom)) / 2e-4

    inner_y, outer_y = rail(inner_x), rail(outer_x, True)
    inner_slope, outer_slope = tangent(inner_x), tangent(outer_x, True)


    ic1 = (line_x + .12, line_y + slope * .12)
    ic2 = (inner_x - .12, inner_y - inner_slope * .12)
    oc1 = (tip_x, tip_y + (outer_y - tip_y) / 2)
    oc2 = (outer_x - .30, outer_y - outer_slope * .30)
    contour = fontforge.contour()
    contour.moveTo(*s.fp(-5, -5))
    contour.lineTo(*s.fp(inner_x, -5))
    contour.lineTo(*s.fp(inner_x, inner_y))
    contour.cubicTo(*s.fp(*ic2), *s.fp(*ic1), *s.fp(line_x, line_y))
    contour.lineTo(*s.fp(tip_x, tip_y))
    contour.cubicTo(*s.fp(*oc1), *s.fp(*oc2), *s.fp(outer_x, outer_y))
    contour.lineTo(*s.fp(-5, outer_y))
    contour.closed = True
    cap = fontforge.layer()
    cap += contour
    cap.correctDirection()
    cap.exclude(hook)
    cap.correctDirection()
    return cap


def _draw_redrawn(ch, s, v):

    parts = _draw_squared(ch, s, v)
    if s.axes.flush_terminals and ch == "g" and s.axes.g_form == "single":


        offset = (v.LSV - s.pen[0]) * s.axes.pen[0] / 2
        edge = 13.25 + offset - s.pen[0] * s.axes.pen[0] / 2
        top = _outer_rail(parts[0], edge) + BASE - CAP
        parts[1] = s.clip(parts[1], y0=top)
    if not s.axes.flush_terminals or ch not in "abdmnru":
        return parts
    half = s.pen[0] * s.axes.pen[0] / 2


    if ch in "mnr":
        x = 2.85 * s.axes.m_width if ch == "m" else 2.25 if ch == "r" else 2.75
        stem, branch, side, bottom = 0, 1, 1, False
    else:
        x = {"a": 12.8, "b": 2.775, "d": 13.25, "u": 12.75}[ch]
        stem, branch = (1, 0) if ch == "u" else (0, 1)
        side, bottom = (1 if ch == "b" else -1), True
    y = _outer_rail(parts[branch], x + side * half, bottom=bottom) + BASE - CAP


    if y < BASE:
        y = BASE - (BASE - y) / s.axes.x_height
    parts[stem] = (s.clip(parts[stem], y1=y) if bottom
                   else s.clip(parts[stem], y0=y))
    return parts


_ROUND_LETTERS = "ABCDOPQRSUVabcdeghmnopqrstu"
_KAPPA = 4 * (math.sqrt(2) - 1) / 3


def _ellipse(s, left, top, right, bottom):

    cx, cy = (left + right) / 2, (top + bottom) / 2
    rx, ry = (right - left) / 2, (bottom - top) / 2
    kx, ky = _KAPPA * rx, _KAPPA * ry
    return s.curve((cx, top), (cx + kx, top), (right, cy - ky), (right, cy),
                   (right, cy + ky), (cx + kx, bottom), (cx, bottom),
                   (cx - kx, bottom), (left, cy + ky), (left, cy),
                   (left, cy - ky), (cx - kx, top), (cx, top), closed=True)


def _ellipse_arc(s, left, top, right, bottom, start, end):

    cx, cy = (left + right) / 2, (top + bottom) / 2
    rx, ry = (right - left) / 2, (bottom - top) / 2
    count = math.ceil(abs(end - start) / (math.pi / 2))
    step = (end - start) / count
    point = lambda a: (cx + rx * math.cos(a), cy + ry * math.sin(a))
    tangent = lambda a: (-rx * math.sin(a), ry * math.cos(a))
    controls = []
    for i in range(count):
        a, b = start + i * step, start + (i + 1) * step
        p, q = point(a), point(b)
        ta, tb = tangent(a), tangent(b)
        k = 4 / 3 * math.tan(step / 4)
        controls.extend(((p[0] + k * ta[0], p[1] + k * ta[1]),
                         (q[0] - k * tb[0], q[1] - k * tb[1]), q))
    return s.curve(point(start), *controls)


def _round_arch(s, left, right, top, bottom):

    radius = (right - left) / 2
    cy, cx = top + radius, (left + right) / 2
    k = _KAPPA * radius
    return s.curve((left, bottom), (left, bottom - 1), (left, cy + 1), (left, cy),
                   (left, cy - k), (cx - k, top), (cx, top),
                   (cx + k, top), (right, cy - k), (right, cy),
                   (right, cy + 1), (right, bottom - 1), (right, bottom))


def _draw_round(ch, s, v):


    sv, sh = s.pen
    if ch == "A":
        return [s.line((10.1, 2.1), (1.7, BASE), box=(-5, 2.1, 25, BASE)),
                s.line((10.3, 2.1), (18.7, BASE), box=(-5, 2.1, 25, BASE)),
                s.hline(14.1, 4.4, 15.7)]
    if ch == "V":
        return [s.line((2.0, 2.1), (10.0, BASE), box=(-5, 2.1, 25, BASE)),
                s.line((18.8, 2.1), (10.8, BASE), box=(-5, 2.1, 25, BASE))]
    if ch in "COQ":
        parts = ([_ellipse_arc(s, 2.6, v.CT, 17.4, v.CB, .34, 2 * math.pi - .34)]
                 if ch == "C" else [_ellipse(s, 2.6, v.CT, 17.4, v.CB)])
        if ch == "Q":
            parts.append(s.line((10, 14.5), (15.3, 20.5), ext=1,
                                box=(-5, 14.3, 25, 21.7)))
        return parts
    if ch in "DP":
        left, right = 2.3, 15.6 if ch == "D" else 15.0
        top, bottom = v.BT, v.BB if ch == "D" else 11.85
        cy, ry = (top + bottom) / 2, (bottom - top) / 2


        x = 8.0 if ch == "D" else 8.5
        rx = right - x
        return [s.vline(left, 2.1, BASE),
                s.curve((left, top), (left + 1, top), (x - 1, top), (x, top),
                        (x + _KAPPA * rx, top), (right, cy - _KAPPA * ry), (right, cy),
                        (right, cy + _KAPPA * ry), (x + _KAPPA * rx, bottom), (x, bottom),
                        (x - 1, bottom), (left + 1, bottom), (left, bottom))]
    if ch == "B":
        left, x = 2.3, 8.0
        parts = [s.vline(left, 2.1, BASE)]
        for top, bottom, right in ((v.BT, 10.2, 14.4), (10.2, v.BB, 15.0)):
            cy, ry, rx = (top + bottom) / 2, (bottom - top) / 2, right - x
            parts.append(s.curve((left, top), (left + 1, top), (x - 1, top), (x, top),
                                 (x + _KAPPA * rx, top), (right, cy - _KAPPA * ry), (right, cy),
                                 (right, cy + _KAPPA * ry), (x + _KAPPA * rx, bottom), (x, bottom),
                                 (x - 1, bottom), (left + 1, bottom), (left, bottom)))
        return parts
    if ch == "R":
        parts = _draw_round("P", s, v)
        parts.append(s.line((9.0, 11.65), (15.0, BASE), ext=1,
                            box=(-5, 11.65 - sh * s.axes.pen[1] / 2, 25, BASE)))
        return parts
    if ch in "Ss":
        left, right = (2.35, 15.3) if ch == "S" else (2.325, 12.525)
        top, bottom = (v.CT, v.CB) if ch == "S" else (v.LT, v.LB)
        height, width = bottom - top, right - left
        cy = top + height * .49
        return [s.curve((right, top + height * .16),
                        (right - width * .16, top - height * .05),
                        (left + width * .08, top - height * .06), (left, top + height * .22),
                        (left - width * .06, top + height * .44),
                        (left + width * .24, cy - height * .02),
                        (left + width * .52, cy + height * .04),
                        (right - width * .19, cy + height * .1),
                        (right + width * .1, bottom - height * .32), (right, bottom - height * .16),
                        (right - width * .13, bottom + height * .06),
                        (left + width * .13, bottom + height * .05), (left, bottom - height * .14))]
    if ch == "U":
        left, right, top, bottom = 2.35, 15.65, 2.1, v.CB
        radius = (right - left) / 2
        cy, cx, k = bottom - radius, (left + right) / 2, _KAPPA * radius
        return [s.curve((left, top), (left, top + 1), (left, cy - 1), (left, cy),
                        (left, cy + k), (cx - k, bottom), (cx, bottom),
                        (cx + k, bottom), (right, cy + k), (right, cy),
                        (right, cy - 1), (right, top + 1), (right, top))]
    if ch in "bdo pq".replace(" ", ""):
        left, right = ((2.775 if ch == "b" else 2.75), 13.75) if ch in "bp" else (2.25, 13.25)
        if ch == "o":
            left, right = 2.2, 13.2
        top, bottom = v.LT, v.LB
        parts = [_ellipse(s, left, top, right, bottom)]
        if ch in "bd":
            parts.append(s.vline(left if ch == "b" else right, 2.1, BASE))
        elif ch in "pq":
            parts.append(s.vline(left if ch == "p" else right, 6.2, v.DS))
        return parts
    if ch in "ce":
        left, right = 2.2, 13.1
        cy = (v.LT + v.LB) / 2
        if ch == "c":
            return [_ellipse_arc(s, left, v.LT, right, v.LB, .45, 2 * math.pi - .45)]
        return [_ellipse_arc(s, left, v.LT, right, v.LB, .72, 2 * math.pi),
                s.hline(cy, left, right)]
    if ch == "a":
        left, right = 2.45, 12.8
        top, bottom = v.LT, v.LB
        cy = top + (bottom - top) * .38


        center, rx, ry = (left + right) / 2, (right - left) / 2, 3.0
        return [s.curve((left, top + ry),
                        (left, top + ry - _KAPPA * ry),
                        (center - _KAPPA * rx, top), (center, top),
                        (center + _KAPPA * rx, top),
                        (right, top + ry - _KAPPA * ry), (right, top + ry),
                        (right, top + ry + 1), (right, bottom - 2.0), (right, bottom - .6),
                        (right, bottom), (right + .5, bottom), (right + 1.15, bottom)),
                s.curve((right, cy), (right - 2, cy), (left, cy + .2), (left, cy + 3.2),
                        (left, bottom + .5), (right - 2.0, bottom + .5), (right, bottom - 2.0))]
    if ch in "hnmr":
        left = 2.25 if ch == "r" else 2.75 if ch != "m" else 2.85
        top = v.XT + sh * s.axes.pen[1] / (2 * s.axes.x_height)
        if ch == "r":
            right = 9.0
            cy = top + (right - left) / 2
            cx, k = (left + right) / 2, _KAPPA * (right - left) / 2
            return [s.vline(left, 6.2, BASE),
                    s.curve((left, cy + 1), (left, cy + .5), (left, cy), (left, cy),
                            (left, cy - k), (cx - k, top), (cx, top),
                            (cx + k / 2, top), (right - .2, top), (right, top + .15))]
        right = 17.9 if ch == "m" else 12.75
        stems = [left, (left + right) / 2, right] if ch == "m" else [left, right]
        stem_top = (top + (stems[1] - left) / 2 if ch == "m" else
                    2.1 if ch == "h" else 6.2)
        return [s.vline(left, stem_top, BASE),
                *[_round_arch(s, a, b, top, BASE) for a, b in zip(stems, stems[1:])]]
    if ch == "u":
        left, right = 2.75, 12.75
        bottom = BASE - sh * s.axes.pen[1] / (2 * s.axes.x_height)
        radius = (right - left) / 2
        cy, cx, k = bottom - radius, (left + right) / 2, _KAPPA * radius
        return [s.vline(right, 6.2, BASE),
                s.curve((left, 6.2), (left, 7.2), (left, cy - 1), (left, cy),
                        (left, cy + k), (cx - k, bottom), (cx, bottom),
                        (cx + k, bottom), (right, cy + k), (right, cy),
                        (right, cy - 1), (right, 7.2), (right, 6.2))]
    if ch == "t":
        bottom = BASE - sh * s.axes.pen[1] / (2 * s.axes.x_height)
        return [s.stroke([(5.75, 2.82), (5.75, bottom), (10.0, bottom)], [0, 2.2, 0]),
                s.hline(7.2, 1.5, 10.0)]
    if ch == "g":
        body_top = BASE - (BASE - 6.2) * s.axes.x_height
        top = body_top - OVERSHOOT + sh * s.axes.pen[1] / 2
        bottom = BASE + OVERSHOOT - sh * s.axes.pen[1] / 2
        descent = BASE + 3.7 * s.axes.descender - sh * s.axes.pen[1] / 2
        if s.axes.g_form == "single":


            offset = (v.LSV - s.pen[0]) * s.axes.pen[0] / 2
            left, right = 2.25 - offset, 13.25 + offset
            if s.axes.g_hook == "foot":


                foot = BASE + 4.24 * s.axes.descender - sh * s.axes.pen[1] / 2
                tip = BASE + .915 * s.axes.descender
                rx, ry = 6.0, 2.575 * s.axes.descender
                center, turn = right - rx, foot - ry
                terminal = left + .8558
                lrx, lry = 3.1, 1.1 * s.axes.descender
                lcy, lcx = foot - lry, terminal + lrx
                hook = s.curve((right, body_top), (right, body_top + 1),
                               (right, turn - 1), (right, turn),
                               (right, turn + _KAPPA * ry),
                               (center + _KAPPA * rx, foot), (center, foot),
                               (center - (center-lcx)/3, foot),
                               (lcx + (center-lcx)/3, foot), (lcx, foot),
                               (lcx - _KAPPA * lrx, foot),
                               (terminal, lcy + _KAPPA * lry), (terminal, lcy),
                               (terminal, lcy - (lcy-tip)/3),
                               (terminal, tip + (lcy-tip)/3), (terminal, tip))
                return [_ellipse(s, left, top, right, bottom), hook]
            if s.axes.g_hook == "symmetric":


                descent = BASE + 4.24 * s.axes.descender - sh * s.axes.pen[1] / 2
                ry = 2.114 * s.axes.descender
                cy, cx, rx = descent - ry, (left + right) / 2, (right - left) / 2
                terminal = BASE + .915 * s.axes.descender
                upturn = cy - terminal
                hook = s.curve((right, body_top), (right, body_top + 1),
                               (right, cy - 1), (right, cy),
                               (right, cy + _KAPPA * ry),
                               (cx + _KAPPA * rx, descent), (cx, descent),
                               (cx - _KAPPA * rx, descent),
                               (left, cy + _KAPPA * ry), (left, cy),
                               (left, cy - upturn / 3),
                               (left, terminal + upturn / 3), (left, terminal))
                return [_ellipse(s, left, top, right, bottom),
                        _attribute_g_terminal(s, hook)]
            return [_ellipse(s, left, top, right, bottom),
                    s.curve((right, body_top), (right, body_top + 1),
                            (right, descent - 3), (right, descent - 2.8),
                            (right, descent + 1), (left + 1, descent + 1), (left, descent - .5))]
        upper_bottom = BASE - 6.45 * s.axes.x_height
        lower_top = BASE - 2.95 * s.axes.x_height
        return [_ellipse(s, 3.425, top, 13.9, upper_bottom),
                s.hline(top, 13.9, 16.9),
                s.stroke([(7, upper_bottom), (3.7, upper_bottom), (3.7, lower_top),
                          (7, lower_top)], [0, 1.6, 1.6, 0]),
                _ellipse(s, 2.825, lower_top, 14.85, descent)]
    raise KeyError(ch)


def _quote_mark(s, x=0):

    return [s.stroke([(3.0+x, 2.185), (5.4+x, 2.185),
                      (5.4+x, 4.235), (3.0+x, 4.235)], .4, True,
                     pen=(2.35, 1.6)),
            s.fill(3.0+x, 2.185, 5.4+x, 4.235),
            s.curve((5.4+x, 3.65), (5.4+x, 5.4), (4.3+x, 6.65),
                    (2.9+x, 7.45), pen=(2.35, 1.6))]


def _draw_symbol(ch, s):
    if ch == "’":
        return _quote_mark(s)
    if ch == "”":
        return _quote_mark(s) + _quote_mark(s, 5.6)
    if ch == "−":
        return [s.hline(11.2143, 1.7, 13.6)]
    raise KeyError(ch)


def _contours(layer):

    out = []
    for contour in layer:
        pts = list(contour)
        if not pts:
            continue
        k = next(i for i, p in enumerate(pts) if p.on_curve)
        pts = pts[k:] + pts[:k] + [pts[k]]
        cv = lambda p: (p.x / U, CAP - p.y / U)
        segments, controls = [cv(pts[0])], []
        for point in pts[1:]:
            if point.on_curve:
                segments.append(("C", *controls, cv(point)) if controls else ("L", cv(point)))
                controls = []
            else:
                controls.append(cv(point))
        out.append(segments)
    return out


@lru_cache(maxsize=64)
def glyphs(axes=Axes()):


    spec = importlib.util.spec_from_file_location(__package__ + "._menu_wo1_drawings", Path(__file__).with_name("wo1vec.py"))
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    s = _Skeleton(axes)
    v.S = s


    v.BT = v.T + v.CSH * axes.pen[1] / 2
    v.BB = v.B - v.CSH * axes.pen[1] / 2
    v.LX = v.XT + v.LSH * axes.pen[1] / (2 * axes.x_height if axes.flush_terminals else 2)


    v.CT = v.T - OVERSHOOT + v.CSH * axes.pen[1] / 2
    v.CB = BASE + OVERSHOOT - v.CSH * axes.pen[1] / 2
    v.LT = v.XT + (-OVERSHOOT + v.LSH * axes.pen[1] / 2) / axes.x_height
    v.LB = BASE + (OVERSHOOT - v.LSH * axes.pen[1] / 2) / axes.x_height
    for name in ("fp", "stroke", "fill", "poly", "curve", "line", "cut", "kjoin",
                 "aimed", "vline", "hline", "rring"):
        setattr(v, name, getattr(s, name))
    out = {}
    for ch, draw in v.G.items():
        s.character = ch
        v.SV, v.SH = (v.LSV, v.LSH) if ch.islower() else (v.CSV, v.CSH)
        s.pen = ((v.SV * axes.g_pen[0], v.SH * axes.g_pen[1])
                 if ch == "g" else (v.SV, v.SH))
        parts = (_draw_round(ch, s, v) if axes.construction == "round" and ch in _ROUND_LETTERS
                 else _draw_redrawn(ch, s, v) if ch in REDRAWN + JOINTS else draw())
        layer = merge(parts)


        x0, y0, x1, y1 = layer.boundingBox()
        box = (x0 / U, CAP - y1 / U, x1 / U, CAP - y0 / U)
        if ch == "g" and axes.g_form == "single" and axes.g_hook == "foot":


            half = v.LSV * axes.pen[0] * U / 2
            bar = v.LSH * axes.pen[1]
            crown = BASE - (BASE - 6.2) * axes.x_height - OVERSHOOT + bar / 2
            foot = BASE + 4.24 * axes.descender - bar / 2
            box = ((2.25 * U - half) / U,
                   CAP - ((BASE - crown) * U + bar * U / 2) / U,
                   (13.25 * U + half) / U,
                   CAP - ((BASE - foot) * U - bar * U / 2) / U)
        out[ch] = (_contours(layer), box)


    for ch in "’”−":
        s.character = ch
        s.pen = (v.CSV, v.CSH)
        layer = merge(_draw_symbol(ch, s))
        x0, y0, x1, y1 = layer.boundingBox()
        out[ch] = (_contours(layer), (x0 / U, CAP - y1 / U, x1 / U, CAP - y0 / U))
    return out
