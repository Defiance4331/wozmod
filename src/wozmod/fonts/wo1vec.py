"""Wo1vec rendering support."""
import math

import fontforge

from .skeleton import Skeleton, merge


ADV = (
    8, 8, 11, 18, 19, 21, 21, 7, 10, 10, 13, 17, 7, 12, 7, 13, 19, 11, 19, 19,
    20, 19, 19, 19, 20, 19, 8, 8, 15, 17, 15, 19, 21, 21, 19, 21, 19, 18, 18, 21,
    19, 7, 18, 18, 17, 21, 20, 21, 19, 21, 19, 19, 20, 19, 21, 21, 20, 20, 19, 9,
    24, 9, 13, 15, 24, 17, 17, 16, 17, 17, 13, 19, 16, 9, 10, 16, 7, 21, 16, 17,
    17, 17, 13, 16, 14, 16, 18, 21, 18, 17, 16, 24, 24, 24,
)
SURROGATE_ADV = {'Ō': 21, 'ō': 17, 'Ū': 19, 'ū': 16, 'ï': 13}


def adv(c):
    return SURROGATE_ADV.get(c) or ADV[ord(c) - 0x20]

U = 50
BASE = 19.3
T, B, XT, DS = 2.1, 19.3, 6.2, 23.0
SV, SH = 2.6, 2.2
CSV, CSH = 2.65, 2.25
LSV, LSH = 2.45, 2.15
THIN = (2.3, 2.1)
WPEN = (2.1, 1.95)
CT, CB = 2.85, 18.9
LT, LB = 7.1, 18.9
BT, BB = T + CSH / 2, B - CSH / 2
LX = XT + SH / 2
RC, RL, RD = 5.0, 3.3, 4.0
MAC = 1.8
DK = 0.93


S = Skeleton(BASE, (SV, SH), DK, U)
fp, stroke, fill, poly, curve, line = S.fp, S.stroke, S.fill, S.poly, S.curve, S.line
cut, kjoin, aimed, vline, hline, rring = S.cut, S.kjoin, S.aimed, S.vline, S.hline, S.rring
KS, KY = 0.0, 0.0


CAP = dict(box=(-5, T, 25, B))
LOW = dict(box=(-5, XT, 25, B))
DOT_W, DOT_H = 3.3, 3.3


def dot(cx, y0, w=DOT_W, h=DOT_H):
    return fill(cx - w / 2, y0, cx + w / 2, y0 + h)


def comma(x0, wd, y0=16.0):

    yb = y0 + 3.0
    return [fill(x0, y0, x0 + wd, yb),
            poly([(x0 + 2.1, yb - 0.5), (x0 + wd, yb - 0.5), (x0 + wd, yb), (x0 + 2.4, 22.2), (x0, 22.2)])]


def wedge(cx, top, bot, wt, wb):

    return poly([(cx - wt / 2, top), (cx + wt / 2, top), (cx + wb / 2, bot), (cx - wb / 2, bot)])


G = {}


def glyph(ch):
    def deco(fn):
        G[ch] = fn
        return fn
    return deco


@glyph('A')
def _():
    return [line((9.93, T), (2.41, B), **CAP), line((10.17, T), (17.69, B), **CAP), hline(14.1, 4.7, 15.4)]


@glyph('B')
def _():
    return [vline(2.3, T, B),
            stroke([(2.3, BT), (14.4, BT), (14.4, 10.2), (2.3, 10.2)], [0, 2.0, 4.0, 0]),
            stroke([(2.3, 10.2), (15.0, 10.2), (15.0, BB), (2.3, BB)], [0, 3.5, 2.4, 0])]


@glyph('C')
def _():
    return [stroke([(17.05, 8.5), (17.05, CT), (2.7, CT), (2.7, CB), (17.05, CB), (17.05, 13.0)], RC)]


@glyph('D')
def _():
    return [vline(2.3, T, B), stroke([(2.3, BT), (15.6, BT), (15.6, BB), (2.3, BB)], [0, 4.5, 4.5, 0])]


@glyph('E')
def _():
    return [vline(2.3, T, B), hline(BT, 2.3, 15.3), hline(10.15, 2.3, 14.3), hline(BB, 2.3, 15.5)]


@glyph('F')
def _():
    return [vline(2.3, T, B), hline(BT, 2.3, 15.3), hline(10.7, 2.3, 14.3)]


@glyph('G')
def _():
    return [stroke([(17.2, 8.0), (17.2, CT), (2.75, CT), (2.75, CB), (17.2, CB), (17.2, 11.0), (9.0, 11.0)],
                   [0, RC, RC, RC, RC, 0, 0])]


@glyph('H')
def _():
    return [vline(2.3, T, B), vline(15.3, T, B), hline(10.15, 2.3, 15.3)]


@glyph('I')
def _():
    return [vline(3.3, T, B)]


@glyph('J')
def _():
    return [stroke([(14.65, T), (14.65, CB), (2.75, CB), (2.75, 13.0)], [0, 3.2, 3.2, 0])]


@glyph('K')
def _():
    arm = lambda y: 12.34 - 1.1675 * (y - 3.5)
    leg = lambda y: 7.5 + 1.0167 * (y - 12.5)
    jy = (12.34 + 1.1675 * 3.5 - 7.5 + 1.0167 * 12.5) / (1.1675 + 1.0167)
    return [vline(2.25, T, B)] + kjoin((arm(T), T), (arm(jy) - KS, jy + KY), (leg(B), B), 2.25, (2.25, T, 25, B))


@glyph('L')
def _():
    return [vline(2.3, T, B), hline(B - 2.1 / 2, 2.3, 15.0, pen=(SV, 2.1))]


@glyph('M')
def _():
    d = dict(pen=THIN, **CAP)
    return [vline(2.15, T, B, pen=THIN), vline(17.15, T, B, pen=THIN),
            line((3.28, T), (9.47, B), **d), line((16.02, T), (9.83, B), **d)]


@glyph('N')
def _():
    x = lambda y: 7.94 + 0.672 * (y - 8.5)
    return [vline(3.0, T, B), vline(16.2, T, B), line((x(T), T), (x(B), B), box=(3.0, T, 16.2, B))]


@glyph('O')
def _():
    return [rring(2.6, CT, 17.4, CB, RC)]


@glyph('P')
def _():
    return [vline(2.25, T, B), stroke([(2.25, BT), (15.0, BT), (15.0, 11.85), (2.25, 11.85)], [0, 3.0, 3.0, 0])]


@glyph('Q')
def _():
    return [rring(2.8, CT, 17.2, CB, 4.75),
            line((10.0, 14.5), (15.3, 20.5), ext=3.0, box=(-5, 14.3, 25, 21.7))]


@glyph('R')
def _():
    leg = lambda y: 14.17 + 0.274 * (y - 13.5)
    return [vline(2.77, T, B), stroke([(2.77, BT), (14.8, BT), (14.8, 11.0), (2.77, 11.0)], [0, 2.4, 2.4, 0]),
            line((leg(11.0), 11.0), (leg(B), B), ext=1.0, box=(-5, 10.0, 25, B))]


def s_curve(xl, xr, xrb, top, bot, term_t, term_b, spine, slope, r_out, r_in, r_bot=None):


    r_bot = r_out if r_bot is None else r_bot
    return stroke([(xr, term_t), (xr, top), (xl, top), (xl, spine - slope * (4 - xl)),
                   (xrb, spine + slope * (xrb - 4)), (xrb, bot), (xl, bot), (xl, term_b)],
                  [0, r_out, r_out, r_in, r_in, r_bot, r_bot, 0])


@glyph('S')
def _():
    return [s_curve(2.35, 15.0, 15.7, 3.15, 18.85, 8.0, 14.0, 10.05, 0.131, RC - 1.0, 2.6)]


@glyph('T')
def _():
    return [hline(BT, 1.5, 18.0), vline(9.75, T, B)]


@glyph('U')
def _():
    return [stroke([(2.35, T), (2.35, CB), (15.65, CB), (15.65, T)], [0, 4.5, 4.5, 0])]


@glyph('V')
def _():
    return [line((2.68, T), (9.62, B), **CAP), line((17.16, T), (10.22, B), **CAP)]


@glyph('W')
def _():
    d = dict(pen=WPEN, **CAP)
    return [line((2.3, T), (5.45, B), **d), line((9.25, T), (6.5, B), **d),
            line((10.35, T), (13.1, B), **d), line((17.3, T), (14.15, B), **d)]


@glyph('X')
def _():
    return [line((3.4, T), (16.3, B), **CAP), line((15.66, T), (2.76, B), **CAP)]


@glyph('Y')
def _():
    return [line((2.76, T), (9.8, 13.1), ext=2.0, box=(-5, T, 9.8 + SV / 2, B)),
            line((16.84, T), (9.8, 13.1), ext=2.0, box=(9.8 - SV / 2, T, 25, B)), vline(9.8, 12.0, B)]


@glyph('Z')
def _():
    return [hline(BT, 2.5, 16.0), hline(BB, 1.55, 16.47),
            aimed((16.0, BT + SH / 2), (1.55, BB - SH / 2), (SV * DK, SH * DK), (1.55, BT, 16.0, BB))]


@glyph('0')
def _():
    return [rring(2.85, CT, 15.55, CB, 4.35)]


@glyph('1')
def _():
    return [hline(BT, 1.5, 7.65), vline(7.65, T, B)]


@glyph('2')
def _():
    return [stroke([(3.0, 8.0), (3.0, 3.15), (15.65, 3.15), (15.65, 10.0), (2.85, 13.9), (2.85, 18.15), (16.7, 18.15)],
                   [0, RD, RD, 2.0, 2.5, 0, 0])]


@glyph('3')
def _():

    return [stroke([(2.95, 6.75), (2.95, CT), (15.4, CT), (15.4, 10.15), (5.0, 10.15)], [0, 3.5, 3.0, 3.4, 0]),
            stroke([(5.0, 10.15), (15.7, 10.15), (15.7, CB), (2.65, CB), (2.65, 13.8)], [0, 4.15, 3.8, 3.7, 0])]


@glyph('4')
def _():
    x = lambda y: 8.8 - 0.95 * (y - 7.5)
    return [vline(13.85, T, B), line((x(T), T), (x(14.85), 14.85), ext=1.0, box=(1.7, T, 13.85 + SV / 2, 14.85)),
            hline(14.85, 1.7, 17.7)]


@glyph('5')
def _():
    return [hline(3.15, 3.3, 16.0), vline(3.3, 3.15 - SH / 2, 12.25),
            stroke([(3.3, 9.15), (15.6, 9.15), (15.6, CB), (2.75, CB), (2.75, 14.75)], [0, 3.4, 3.7, 3.3, 0])]


@glyph('6')
def _():

    return [stroke([(15.45, 5.65), (15.45, CT), (2.35, CT), (2.35, 14.2)], [0, 3.6, 4.6, 0]),
            stroke([(2.35, 9.15), (15.7, 9.15), (15.7, CB), (2.35, CB)], [4.75, 3.9, 4.0, 4.4], True)]


@glyph('7')
def _():
    x = lambda y: 14.48 - 0.742 * (y - 5.5)
    return [hline(BT, 1.53, 16.73), line((x(BT), BT), (x(B), B), ext=2.0, box=(-5, BT, 16.73, B))]


@glyph('8')
def _():
    return [rring(2.8, CT, 15.4, 10.0, 3.2), rring(2.3, 10.0, 15.95, CB, RD)]


@glyph('9')
def _():

    return [stroke([(2.5, CT), (15.7, CT), (15.7, 11.9), (2.5, 11.9)], [4.2, 4.3, 4.3, 3.5], True),
            stroke([(15.7, 7.375), (15.7, CB), (2.75, CB), (2.75, 15.0)], [0, 4.6, 3.5, 0])]


@glyph('a')
def _():
    return [stroke([(2.95, 10.15), (2.95, 7.05), (12.8, 7.05), (12.8, 18.95 + SH / 2)], [0, 3.1, 2.5, 0]),
            stroke([(12.8, 12.4), (2.45, 12.4), (2.45, 18.95), (12.8, 18.95), (12.8, 15.0)], [0, 3.35, 2.75, 3.6, 0])]


@glyph('b')
def _():

    return [vline(2.775, T, 18.75 + SH / 2),
            stroke([(2.775, 7.1), (13.75, 7.1), (13.75, 18.75), (2.775, 18.75)], [4.025, 3.75, 3.75, 4.025], True)]


@glyph('c')
def _():
    return [stroke([(12.75, 10.6), (12.75, LT), (2.2, LT), (2.2, LB), (12.75, LB), (12.75, 14.3)], RL)]


@glyph('d')
def _():
    return [vline(13.25, T, LB + SH / 2), rring(2.25, LT, 13.25, LB, RL)]


@glyph('e')
def _():
    return [stroke([(2.2, 12.1), (13.1, 12.1), (13.1, LT), (2.2, LT), (2.2, LB), (13.1, LB), (13.1, 14.4)],
                   [0, 0, RL, RL, RL, RL, 0])]


@glyph('f')
def _():
    return [stroke([(5.75, B), (5.75, 3.3), (11.0, 3.3)], [0, 2.2, 0]), hline(8.0, 1.7, 11.0)]


@glyph('g')
def _():
    return [stroke([(3.425, 7.075), (13.9, 7.075), (13.9, 12.85), (3.425, 12.85)], [2.55, 2.525, 2.525, 2.55], True),
            hline(7.075, 13.9, 16.475),
            stroke([(7.0, 12.85), (3.7, 12.85), (3.7, 16.35), (7.0, 16.35)], [0, 1.6, 1.6, 0]),
            rring(2.825, 16.35, 14.85, 21.925, 2.7874)]


@glyph('h')
def _():
    return [vline(2.75, T, B), stroke([(2.75, 12.0), (2.75, LX), (12.75, LX), (12.75, B)], [0, RL, RL, 0])]


@glyph('i')
def _():
    return [vline(4.75, XT, B), dot(4.75, 2.0, 3.1, 2.6)]


@glyph('j')
def _():
    return [stroke([(5.75, XT), (5.75, 21.8), (1.2, 21.8)], [0, 2.4, 0]), dot(5.75, 2.0, 3.1, 2.6)]


@glyph('k')
def _():
    arm = lambda y: 10.97 - 1.1 * (y - 6.5)
    leg = lambda y: 7.35 + 0.985 * (y - 14.5)
    jy = (10.97 + 1.1 * 6.5 - 7.35 + 0.985 * 14.5) / (1.1 + 0.985)
    return [vline(2.25, T, B)] + kjoin((arm(XT), XT), (arm(jy) - KS, jy + KY), (leg(B), B), 2.25, (2.25, XT, 25, B))


@glyph('l')
def _():
    return [vline(3.75, T, B)]


@glyph('m')
def _():
    return [vline(2.85, XT, B, pen=THIN),
            stroke([(2.85, 12.0), (2.85, 7.15), (10.1, 7.15), (10.1, B)], [0, 2.4, 2.4, 0], pen=THIN),
            stroke([(10.1, 12.0), (10.1, 7.15), (17.15, 7.15), (17.15, B)], [0, 2.4, 2.4, 0], pen=THIN)]


@glyph('n')
def _():
    return [vline(2.75, XT, B), stroke([(2.75, 12.0), (2.75, LX), (12.75, LX), (12.75, B)], [0, RL, RL, 0])]


@glyph('o')
def _():
    return [rring(2.2, LT, 13.25, LB, RL)]


@glyph('p')
def _():
    return [vline(2.75, 7.0 - SH / 2, DS), rring(2.75, 7.0, 13.75, 18.15, RL)]


@glyph('q')
def _():
    return [vline(13.25, 7.0 - SH / 2, DS), rring(2.25, 7.0, 13.25, 18.0, RL)]


@glyph('r')
def _():
    return [vline(2.25, XT, B), stroke([(2.25, 11.0), (2.25, LX), (10.3, LX)], [0, RL, 0])]


@glyph('s')
def _():
    return [s_curve(2.325, 12.075, 12.525, 7.075, 18.925, 10.25, 15.5, 12.325, 0.14, 2.825, 2.125, r_bot=2.8)]


@glyph('t')
def _():
    return [stroke([(5.75, 3.3), (5.75, BB), (11.3, BB)], [0, 2.6, 0]), hline(7.2, 1.5, 11.3)]


@glyph('u')
def _():
    return [stroke([(2.75, XT), (2.75, 18.4), (12.75, 18.4), (12.75, 12.0)], [0, RL, RL, 0]), vline(12.75, XT, 18.4 + SH / 2)]


@glyph('v')
def _():
    return [line((2.67, XT), (8.17, B), **LOW), line((14.23, XT), (8.73, B), **LOW)]


@glyph('w')
def _():
    d = dict(pen=WPEN, **LOW)
    return [line((2.4, XT), (5.7, B), **d), line((9.75, XT), (6.3, B), **d),
            line((10.35, XT), (13.8, B), **d), line((17.7, XT), (14.4, B), **d)]


@glyph('x')
def _():
    return [line((3.35, XT), (13.9, B), **LOW), line((13.35, XT), (2.8, B), **LOW)]


@glyph('y')
def _():
    L = math.hypot(14.68 - 7.03, 21.625 - 4.0)
    tail = ((21.625 - 4.0) / L, (14.68 - 7.03) / L)
    return [cut(line((2.78, XT), (8.3, 17.965), ext=2.5, box=(-5, XT, 25, 25)), (14.68, 4.0), tail, 0),
            stroke([(14.68, 4.0), (7.03, 21.625), (2.2, 21.625)], [0, 3.175, 0], box=(-5, XT, 25, 25))]


@glyph('z')
def _():
    top, bot = 7.0, 18.35
    return [hline(top, 2.0, 13.73), hline(bot, 1.53, 13.9),
            aimed((13.73, top + SH / 2), (1.53, bot - SH / 2), (SV * DK, SH * DK), (1.53, top, 13.73, bot))]


def macron(x0, x1, yc):
    return hline(yc, x0, x1, pen=(SV, MAC))


@glyph('Ō')
def _():
    return [rring(2.6, 5.1, 17.4, CB, 4.2), macron(6.3, 14.0, 1.1)]


@glyph('Ū')
def _():
    return [stroke([(2.35, 3.5), (2.35, CB), (15.65, CB), (15.65, 3.5)], [0, 3.4, 3.4, 0]), macron(5.3, 12.7, 1.1)]


@glyph('ō')
def _():
    return G['o']() + [macron(3.7, 11.5, 3.2)]


@glyph('ū')
def _():
    return G['u']() + [macron(4.0, 11.7, 3.2)]


@glyph('ï')
def _():
    return [vline(5.75, XT, B), dot(2.75, 2.0, 3.1, 2.6), dot(8.75, 2.0, 3.1, 2.6)]


@glyph('!')
def _():
    return [fill(3.025, 2.27, 5.725, 14.75), fill(2.725, 16.55, 6.025, 19.27)]


@glyph('"')
def _():
    return [wedge(3.85, 2.27, 7.95, 2.5, 1.95), wedge(7.15, 2.27, 7.95, 2.5, 1.95)]


@glyph('#')
def _():
    p = dict(pen=(2.1, 2.0), box=(-5, T, 25, B))
    return [line((6.5 + 0.125 * 0.4, T), (4.5 - 0.125 * 0.8, B), **p),
            line((12.55 + 0.128 * 0.4, T), (10.5 - 0.128 * 0.8, B), **p),
            hline(7.0, 2.3, 16.0, pen=(2.1, 2.0)), hline(14.0, 1.5, 15.0, pen=(2.1, 2.0))]


@glyph('$')
def _():
    return [s_curve(2.125, 15.125, 15.6, 3.1, 18.3, 7.0, 14.0, 9.85, 0.119, 3.55, 2.875, r_bot=3.425),
            vline(9.0, 0.3, 21.7, pen=(2.0, 2.0))]


@glyph('%')
def _():
    p = (1.8, 2.0)
    x = lambda y: 16.55 - 0.79 * (y - 2.5)
    return [rring(1.9, 3.0, 7.85, 11.0, 2.0, pen=p), rring(12.0, 11.0, 17.85, 18.85, 2.0, pen=p),
            line((x(2.0), 2.0), (x(19.7), 19.7), pen=(2.2, 2.0), box=(-5, 2.0, 25, 19.7))]


@glyph('&')
def _():
    p, bot = (LSV, LSH), 18.875
    return [stroke([(3.225, 3.075), (14.575, 3.075), (14.575, 9.85), (3.225, 9.85)], [2.55, 3.05, 3.9, 3.4], True, pen=p),
            stroke([(6.5, 10.925), (2.25, 10.925), (2.25, bot), (18.7, bot)], [0, 2.8, 3.05, 0],
                   pen=(LSV * 0.95, LSH * 0.95)),
            line((6.4, 10.575), (16.45, bot), ext=0.0, pen=(LSV * DK, LSH * DK),
                 box=(-5, -5, 16.225 + LSV / 2, bot + LSH * 0.95 / 2)),
            vline(16.225, 12.25, bot, pen=p)]


@glyph("'")
def _():
    return [wedge(3.25, 1.75, 7.75, 3.0, 1.95)]


@glyph('(')
def _():
    return [curve((6.2, -0.8), (3.35, 3.35), (2.2, 6.6), (2.2, 10.5), (2.2, 14.4), (3.35, 17.65), (6.2, 21.8),
                  box=(-5, 0.0, 25, 21.0))]


@glyph(')')
def _():
    return [curve((2.25, -0.8), (5.1, 3.35), (6.25, 6.6), (6.25, 10.5), (6.25, 14.4), (5.1, 17.65), (2.25, 21.8),
                  box=(-5, 0.0, 25, 21.0))]


@glyph('*')
def _():
    p = (1.7, 1.78)
    return [vline(6.125, 2.25, 11.0, pen=p), line((2.175, 4.4), (10.075, 8.78), ext=0.0, pen=p),
            line((10.075, 4.4), (2.175, 8.78), ext=0.0, pen=p)]


@glyph('+')
def _():
    return [vline(8.0, 3.3, 16.3, pen=(2.0, 2.0)), hline(10.0, 1.5, 14.5, pen=(2.0, 2.0))]


@glyph(',')
def _():
    return comma(1.0, 3.47)


@glyph('-')
def _():
    return [hline(12.85, 1.7, 10.0)]


@glyph('.')
def _():
    return [fill(1.6, 16.0, 4.9, 19.3)]


@glyph('/')
def _():
    x = lambda y: 9.495 - 0.419 * (y - 2.5)
    return [line((x(T), T), (x(B), B), pen=(SV * DK * 0.915, SH * DK * 0.915), **CAP)]


@glyph(':')
def _():
    return [fill(2.0, 6.0, 5.3, 9.3), fill(2.0, 16.0, 5.3, 19.3)]


@glyph(';')
def _():
    return [fill(2.0, 6.0, 5.3, 9.3)] + comma(2.0, 3.3)


def chevron(xv, xt, yv, h, x0, x1, k=1.0):

    b = dict(box=(min(x0, x1), -5, max(x0, x1), 25), pen=(SV * DK * k, SH * DK * k))
    return [line((xv, yv), (xt, yv - h), **b), line((xv, yv), (xt, yv + h), **b)]


@glyph('<')
def _():
    return chevron(0.96, 12.75, 7.86, 5.6, 0.975, 12.525, 0.915)


@glyph('=')
def _():
    return [hline(6.0, 1.0, 14.73, pen=(2.0, 2.0)), hline(11.0, 1.0, 14.73, pen=(2.0, 2.0))]


@glyph('>')
def _():
    return chevron(12.59, 0.8, 7.86, 5.6, 12.575, 1.025, 0.915)


@glyph('?')
def _():
    return [stroke([(2.275, 8.45), (2.275, 2.95), (15.4, 2.95), (15.4, 9.3), (8.275, 11.8), (8.275, 15.0)],
                   [0, 3.2, 3.2, 2.4, 1.85, 0]), dot(8.275, 16.1, 3.45, 2.9)]


@glyph('@')
def _():
    ty = 14.8
    tl = ty + (2.025 - 1.725) / 2
    return [stroke([(15.05, 16.75), (15.05, 18.575), (1.975, 18.575), (1.975, 3.075), (18.025, 3.075), (18.025, tl),
                    (9.5, tl)], [0, 1.825, 6.875, 6.925, 5.9, 4.4, 0], pen=(1.75, 1.725)),
            stroke([(7.584, 6.05), (14.011, 6.05), (11.699, ty), (4.859, ty)], [2.8, 0, 2.55, 2.5], True,
                   pen=(2.2, 2.025))]


@glyph('[')
def _():
    return [stroke([(6.7, 2.05), (2.15, 2.05), (2.15, 20.95), (6.7, 20.95)], 0, pen=(2.4, 2.1))]


@glyph(']')
def _():
    return [stroke([(1.3, 2.05), (5.85, 2.05), (5.85, 20.95), (1.3, 20.95)], 0, pen=(2.4, 2.1))]


@glyph('^')
def _():
    return [stroke([(5.78 - 0.525 * 9.9, 12.3), (5.78, 2.4), (5.78 + 0.525 * 9.9, 12.3)],
                   pen=(2.1, 2.0), box=(0.9, 2.0, 10.6, 10.3))]


@glyph('_')
def _():
    return [fill(1.15, 21.26, 12.75, 22.94)]


def make(only=None):
    f = fontforge.font()
    f.encoding = 'UnicodeFull'
    f.em = 1200
    f.ascent = round(BASE * U)
    f.descent = 1200 - f.ascent
    f.fontname, f.familyname, f.fullname = 'WO1Redraw', 'WO1 Redraw', 'WO1 Redraw'
    for k in ('hhea_ascent', 'os2_typoascent', 'os2_winascent'):
        setattr(f, k + '_add', False)
        setattr(f, k, f.ascent)
    for k in ('hhea_descent', 'os2_typodescent', 'os2_windescent'):
        setattr(f, k + '_add', False)
        setattr(f, k, f.descent if k == 'os2_windescent' else -f.descent)
    sp = f.createChar(0x20)
    sp.width = adv(' ') * U
    global SV, SH
    for ch, fn in G.items():
        if only and ch not in only:
            continue
        SV, SH = (LSV, LSH) if ch.islower() else (CSV, CSH)
        S.pen = (SV, SH)
        l = merge(fn())
        l.round()
        l.addExtrema()
        l.round()
        g = f.createChar(ord(ch))
        g.foreground = l
        g.width = adv(ch) * U
    return f


def build(path, only=None):
    make(only).generate(str(path))
    return path


if __name__ == '__main__':
    import sys
    print(build(sys.argv[1] if len(sys.argv) > 1 else 'wo1-redraw.otf'))
