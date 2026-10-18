import math

import fontforge

from .skeleton import Skeleton, _layer, merge

U = 50
CAP = 45.438
SERIF = 2.114
SEND = 1.066
SXT = 4.451
SXB = 4.644
BRK = 2.025
BRKT = 4.416
TIP = 0.0
BEAK = (4.674, 0.908, 9.783)
COVE = 1.215


PARAMS = {
    "I": dict(w=7.547),
    "T": dict(w=32.955, h=1.869, bh=13.006, f=1.76, xc=16.481, ws=7.532, rise=0.671, rl=12.698),
    "E": dict(w=7.107, e1=26.147, h1=1.831, bh1=11.594, f1=1.57, r1=0.792, e2=20.004, ym=21.821, h2=2.054, su=8.554,
             sd=8.582, e3=27.327, h3=2.34, bh3=13.27, f3=2.386, r3=0.383, fo=1.043, rl1=15.388, rl3=11.412,
             mbw=3.546, mbr=5.719),
    "F": dict(w=7.125, e1=26.519, h1=1.774, bh1=11.722, f1=1.673, r1=0.999, e2=21.522, ym=22.248, h2=2.158,
             su=8.636, sd=8.491, sbr=5.033, fo=0.834, rl1=13.616, mbw=3.431, mbr=5.452),
    "L": dict(w=7.128, str=4.618, e=25.978, h=2.505, bh=15.432, f=2.722, r=0.54, rl=11.663),
    "D": dict(w=7.139,
             o=[[1, 0, 270, 0, 0, 0], [16.547, 0, 0, 0, 0, 6.374], [33.842, 22.705, 90, 90, 17.257, 18.038], [14.092, 45.42, 180, 180, 8.506, 0], [1, 45.42, 180, 270, 0, 0]],
             i=[[6, 2.179, 270, 0, 0, 0], [11.783, 1.958, 0, 0, 0, 9.425], [25.986, 23.656, 90, 90, 11.581, 12.705], [11.99, 43.252, 180, 180, 9.404, 0], [6, 42.309, 180, 270, 0, 0]]),
    "P": dict(w=7.196, sbr=5.061,
             o=[[1, 0, 270, 0, 0, 0], [16.961, -0.2, 0, 0, 0, 7.24], [30.45, 13.103, 90, 90, 8.705, 11.178], [16.79, 27.699, 180, 180, 6.661, 0], [1, 27.0, 180, 270, 0, 0]],
             i=[[6, 2.0, 270, 0, 0, 0], [13.359, 1.741, 0, 0, 0, 5.426], [23.28, 13.157, 90, 90, 6.599, 14.025], [11.903, 25.018, 180, 180, 1.663, 0], [6, 23.594, 180, 270, 0, 0]]),
    "R": dict(w=7.106, sbr=4.584,
             o=[[1, 0, 270, 0, 0, 0], [14.484, -0.4, 0, 0, 0, 8.654], [29.142, 11.169, 90, 90, 8.144, 9.611], [15.209, 23.409, 180, 180, 7.786, 0], [1, 22.7, 180, 270, 0, 0]],
             i=[[6, 1.746, 270, 0, 0, 0], [14.678, 1.834, 0, 0, 0, 1.354], [22.145, 10.806, 90, 90, 7.08, 8.278], [13.334, 21.187, 180, 180, 4.783, 0], [6, 20.06, 180, 270, 0, 0]],
             lt=13.84, lb=27.11, wl=7.775, ly=19.0, tail=4.163, tb=10.36),
    "O": dict(
             o=[[20.019, -1.069, 0, 0, 12.595, 12.166], [39.973, 22.664, 90, 90, 14.731, 14.324], [20.146, 46.446, 180, 180, 12.333, 13.276], [0, 22.754, 270, 270, 13.533, 14.342]],
             i=[[19.55, 1.636, 0, 0, 9.13, 6.626], [32.011, 25.672, 90, 90, 15.422, 7.391], [20.819, 43.745, 180, 180, 8.659, 7.843], [7.897, 20.116, 270, 270, 13.344, 8.793]]),
    "C": dict(
             n=[[35.307, 13.72, 0, 272, 0, 0], [33.813, 0.659, 272, 171.902, 0, 5.655], [19.872, -0.977, 180, 180, 8.744, 11.814], [0, 22.567, 90, 90, 14.519, 16.19], [21.497, 46.47, 0, 0, 12.292, 4.665], [35.096, 43.049, 313.087, 270, 2.104, 0], [35.668, 30.447, 270, 180, 0, 0], [34.729, 30.447, 180, 104.183, 0, 8.964], [22.656, 43.792, 180, 180, 6.73, 9.052], [7.962, 20.519, 270, 270, 13.673, 10.839], [20.772, 1.659, 0, 0, 9.285, 5.957], [33.956, 12.628, 71.233, 180, 8.377, 0]]),
    "G": dict(
             n=[[35.55, 14.155, 0, 272, 0, 0], [34.153, 0.673, 272, 176.32, 0, 7.125], [19.464, -0.987, 180, 180, 9.63, 9.335], [0, 22.344, 90, 90, 15.484, 16.957], [21.974, 46.473, 0, 0, 12.314, 3.756], [36.643, 41.164, 270, 270, 3.24, 0], [33.0, 27.0, 270, 180, 0, 0], [26.0, 27.0, 180, 90, 0, 0], [29.712, 40.829, 90, 180, 0, 2.186], [22.862, 43.739, 180, 180, 8.188, 8.784], [7.96, 20.51, 270, 270, 13.862, 11.55], [20.806, 1.666, 0, 0, 8.634, 5.442], [33.486, 10.813, 68.62, 180, 7.117, 0]],
             sy=24.146, sl=4.058, sr=3.667),
    "S": dict(
             n=[[30.153, 13.741, 0, 272, 0, 0], [28.682, 0.485, 272, 179.705, 0, 8.659], [15.779, -0.981, 180, 180, 7.573, 11.046], [1.218, 10.922, 90, 90, 5.499, 5.674], [10.787, 23.556, 25.008, 25.008, 6.936, 6.248], [26.076, 36.69, 90, 90, 8.209, 5.037], [16.523, 43.72, 180, 180, 4.528, 8.406], [1.916, 29.986, 252.118, 180, 5.458, 0], [0.986, 30.302, 180, 90, 0, 0], [2.636, 42.844, 90, 90, 0, 0.819], [16.515, 46.447, 0, 0, 6.911, 7.616], [32.435, 33.569, 270, 270, 9.839, 5.818], [17.137, 17.085, 198.05, 198.05, 13.469, 4.968], [6.612, 8.354, 270, 270, 4.424, 3.379], [16.34, 1.526, 0, 0, 8.547, 4.74], [28.46, 11.199, 69.35, 0, 6.394, 0]]),
    "U": dict(w=7.139, st=(4.497, 4.49), x1=28.387, wt=2.441, tt=(5.304, 5.254),
             o=[[0, 25.0, 90, 90, 0, 0], [0, 28.983, 90, 90, 0, 12.896], [16.315, 46.482, 0, 0, 8.356, 8.335], [30.8, 29.912, 270, 270, 10.896, 0], [30.8, 25.0, 270, 270, 0, 0]],
             i=[[28.4, 25.0, 90, 90, 0, 0], [28.4, 29.307, 90, 90, 0, 9.636], [18.454, 42.452, 180, 180, 4.761, 5.05], [7.15, 28.687, 270, 270, 10.119, 0], [7.15, 25.0, 270, 270, 0, 0]]),
    "N": dict(x0=1.089, w=2.405, sb=(5.292, 5.76), stl=5.233, dl=-1.639, wd=8.759, sl=0.654, vy=46.424, x1=28.744,
             w1=2.418, s1=(5.769, 5.451), y1=40.0),
    "M": dict(w=2.396, sb=(5.015, 5.437), stl=4.989, dl=0.202, wd=7.134, x2=34.155, w2=2.779, xv=18.015, wv=2.42,
             vy=45.381, x3=32.344, w3=7.309, s3t=(0.0, 4.499), s3b=(4.357, 4.528)),
    "V": dict(wl=7.969, wr=2.743, xr=35.489, xv=18.548, wv=2.674, vy=46.153, stl=(2.89, 5.584), str=(6.95, 3.975)),
    "A": dict(xa=21.293, ya=-0.464, aw=2.421, wl=2.817, sl=0.355, wr=7.825, sr=0.374, yc=30.62, hc=2.369,
             sbl=(3.526, 7.26), sbr=(5.508, 4.238)),
    "Y": dict(xl=6.637, wl=8.589, xr=36.434, wr=2.724, yj=27.915, xs=17.483, ws=7.203, stl=(1.956, 5.174),
             str=(7.505, 2.529), sb=(5.374, 5.233), yjr=25.418),
}


BEARINGS = {"A": (-3.02, -3.23), "C": (1.77, 1.77), "D": (1.37, 1.84), "E": (1.19, 1.89), "F": (0.81, 0.81),
            "G": (2.29, 2.29), "I": (1.67, 0.93), "L": (1.52, 0.06), "M": (0.52, 1.27), "N": (0.6, 0.52),
            "O": (2.14, 2.2), "P": (0.63, 2.84), "Q": (2.14, -6.93), "R": (1.45, -1.15), "S": (2.96, 3.38),
            "T": (-0.66, 0.25), "U": (0.15, -0.41), "V": (-2.92, -1.99), "Y": (-2.13, -2.93)}
SPACE = 19.39
KERN = {}
# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
SETS = {
    "5a93503c1ee64215de74d5196e20c8da883582fea73bffde05b9aab9c85b034a": (0.0, 34.24, 72.99, 116.94, 154.14, 211.3, 264.0, 307.95, 349.91),
    "5ddcad9c51246326705eeab710470ffb4181b75a44e5769971ef05d61068e74d": (0.0, 35.08, 75.04, 112.02, 168.41, 221.11, 263.99, 306.05),
    "0ec1bbbac7a32e191abfb8c624e8119030bb7cfad5bb52f6c4a6b6640bac7d7f": (0.0, 41.64, 81.34, 114.68, 155.67, 225.73, 279.18, 322.94, 364.61),
    "61852a9f98258e346aa2eb2bcdbf6fbb5fd82fa87b6850cc37794145a0571a3d": (0.0, 43.9, 99.21, 151.91, 195.89, 236.82),
    "22b111c17b2c4be356ccf0448633388e32ca2ccd0d97148bcf2b91e09b710906": (0.0, 34.06, 74.96, 110.09, 151.07, 165.1, 201.71, 242.96, 295.32, 347.01, 389.94, 430.95),
    "316a1d8733d73abafc8878c10395423ce5dc3f4aaa07a5191c231d3da726dfa5": (0.0, 39.7, 81.91, 116.88, 151.93, 189.91, 226.13),
    "d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e": (0.0, 42.88, 80.25, 119.06, 139.0, 181.99, 225.98),
    "628db0c7598820b101769164f5228a5096e4f531ee71ee7e5e31d734dd38fe64": (0.0, 39.99, 82.98, 124.24, 163.05, 181.98, 224.09, 265.88),
}


def shape(s, pts):
    xy = [p[:2] for p in pts]
    r = [p[2] if len(p) > 2 else 0.0 for p in pts]
    n = len(xy)

    def tangent(i):
        (ax, ay), (vx, vy), (bx, by) = xy[i - 1], xy[i], xy[(i + 1) % n]
        u = (ax - vx, ay - vy), (bx - vx, by - vy)
        la, lb = math.hypot(*u[0]), math.hypot(*u[1])
        if la < 1e-9 or lb < 1e-9:
            return 0.0
        c = (u[0][0] * u[1][0] + u[0][1] * u[1][1]) / (la * lb)
        th = math.acos(max(-1.0, min(1.0, c)))
        return 0.0 if r[i] <= 0 or th < 1e-3 or abs(th - math.pi) < 1e-3 else r[i] / math.tan(th / 2)
    d = [tangent(i) for i in range(n)]
    k = [1.0] * n
    for i in range(n):
        j = (i + 1) % n
        need, have = d[i] + d[j], math.dist(xy[i], xy[j])
        if need > have:
            k[i] = min(k[i], have / need)
            k[j] = min(k[j], have / need)
    l = _layer(s.contour(xy, [ri * ki for ri, ki in zip(r, k, strict=True)], True))
    l.correctDirection()
    return l


def spline(s, nodes):
    c = fontforge.contour()
    c.moveTo(*s.fp(*nodes[0][:2]))
    n = len(nodes)
    for i in range(n):
        a, b = nodes[i], nodes[(i + 1) % n]
        if a[5] == 0 and b[4] == 0:
            c.lineTo(*s.fp(*b[:2]))
        else:
            ao, ai = math.radians(a[3]), math.radians(b[2])
            c.cubicTo(s.fp(a[0] + a[5] * math.cos(ao), a[1] + a[5] * math.sin(ao)),
                      s.fp(b[0] - b[4] * math.cos(ai), b[1] - b[4] * math.sin(ai)), s.fp(*b[:2]))
    c.closed = True
    l = _layer(c)
    l.correctDirection()
    return l


def minus(a, b):
    b.exclude(a)
    b.correctDirection()
    return b


def stroke(xt, xb, wt, wb, y0=0.0, y1=None, top=(0, 0), bot=(0, 0), brk=None):
    y1 = CAP if y1 is None else y1
    brk = BRK if brk is None else brk
    L = lambda y: xt - wt / 2 + (xb - wb / 2 - xt + wt / 2) * (y - y0) / (y1 - y0)
    R = lambda y: xt + wt / 2 + (xb + wb / 2 - xt - wt / 2) * (y - y0) / (y1 - y0)
    (tl, tr), (bl, br) = top, bot
    pts = [(L(y0) - tl, y0, TIP if tl else 0.0)]
    pts += [(R(y0) + tr, y0, TIP), (R(y0) + tr, y0 + SEND, TIP), (R(y0 + SERIF), y0 + SERIF, brk)] if tr else \
        [(R(y0), y0)]
    pts += [(R(y1 - SERIF), y1 - SERIF, brk), (R(y1) + br, y1 - SEND, TIP), (R(y1) + br, y1, TIP)] if br else \
        [(R(y1), y1)]
    pts += [(L(y1) - bl, y1, TIP), (L(y1) - bl, y1 - SEND, TIP), (L(y1 - SERIF), y1 - SERIF, brk)] if bl else \
        [(L(y1), y1)]
    if tl:
        pts += [(L(y0 + SERIF), y0 + SERIF, brk), (L(y0) - tl, y0 + SEND, TIP)]
    return pts


def stem(x0, x1, top=None, bot=None, y0=0.0, y1=None, brk=None):
    return stroke((x0 + x1) / 2, (x0 + x1) / 2, x1 - x0, x1 - x0, y0, CAP if y1 is None else y1,
                  (SXT, SXT) if top is None else top, (SXB, SXB) if bot is None else bot, brk)


def swell(e, y, d, rise, rl, sgn=1):
    if rise <= 0.01 or rl <= 0.01:
        return []
    return [(e - sgn * rl * (1 - t), y - d * rise * t * t) for t in (0, 0.25, 0.5, 0.75)]


def hook(x0, e, y, d, depth, f, h, rise=0.0, rl=None):
    bw, tw, br = BEAK
    pts = [(x0, y)] + swell(e, y, d, rise, bw if rl is None else rl)
    pts += [(e, y - d * rise, TIP), (e + f, y + d * depth, TIP), (e + f - tw, y + d * depth),
            (e - bw, y + d * h, br), (x0, y + d * h)]
    return pts


def mid_arm(x0, e, ym, h, su, sd, fo=0.0, mbw=None, mbr=None):
    bw, tw, br = BEAK
    bw = bw if mbw is None else mbw
    br = br * 0.8 if mbr is None else mbr
    a, b = ym - h / 2, ym + h / 2
    face = [(e + fo, a - su, TIP), (e, ym, 3.0), (e + fo, b + sd, TIP)] if fo > 0.01 else \
        [(e, a - su, TIP), (e, b + sd, TIP)]
    return [(x0, a), (e - bw, a, br), (e + fo - tw, a - su)] + face + \
        [(e + fo - tw, b + sd), (e - bw, b, br), (x0, b)]


def edge_normal(p, q, side):
    L = math.dist(p, q)
    return (-side * (q[1] - p[1]) / L, side * (q[0] - p[0]) / L)


def cove(s, x, y, dx, dy, r=None, m=0.0):
    r = max(0.05, COVE if r is None else r)
    o, k = 0.3, 0.5523
    c = fontforge.contour()
    c.moveTo(*s.fp(x - dx * o, y - dy * o))
    c.lineTo(*s.fp(x + dx * r, y + m * dx * r - dy * o))
    c.lineTo(*s.fp(x + dx * r, y + m * dx * r))
    c.cubicTo(s.fp(x + dx * r * (1 - k), y + m * dx * r * (1 - k)), s.fp(x, y + dy * r * (1 - k)),
              s.fp(x, y + dy * r))
    c.lineTo(*s.fp(x - dx * o, y + dy * r))
    c.closed = True
    l = _layer(c)
    l.correctDirection()
    return l


BUILD = {}


def glyph(ch):
    def deco(fn):
        BUILD[ch] = fn
        return fn
    return deco


@glyph("I")
def _(s, p):
    return [shape(s, stem(0, p["w"]))]


@glyph("T")
def _(s, p):
    w, h, bh, f, r = p["w"], p["h"], p["bh"], p["f"], p["rise"]
    bw, tw, br = BEAK
    rl = p.get("rl", bw)
    bar = [(-f, bh, TIP), (tw - f, bh), (bw, h, br), (w - bw, h, br), (w + f - tw, bh), (w + f, bh, TIP),
           (w, -r, TIP)]
    bar += swell(w, 0, 1, r, rl)[::-1] + swell(0, 0, 1, r, rl, -1)
    bar.append((0, -r, TIP))
    ws, xc = p["ws"], p["xc"]
    return [shape(s, bar), shape(s, stem(xc - ws / 2, xc + ws / 2, (0, 0), (SXB, SXB), y0=1.0)),
            cove(s, xc - ws / 2, h, -1, 1), cove(s, xc + ws / 2, h, 1, 1)]


@glyph("E")
def _(s, p):
    w = p["w"]
    ym, h2 = p["ym"], p["h2"]
    return [shape(s, stem(0, w, (SXT, 0), (SXB, 0))),
            shape(s, hook(1, p["e1"], 0, 1, p["bh1"], p["f1"], p["h1"], p["r1"], p.get("rl1"))),
            shape(s, mid_arm(1, p["e2"], ym, h2, p["su"], p["sd"], p.get("fo", 0.0), p.get("mbw"), p.get("mbr"))),
            shape(s, hook(1, p["e3"], CAP, -1, p["bh3"], p["f3"], p["h3"], p["r3"], p.get("rl3"))),
            cove(s, w, p["h1"], 1, 1), cove(s, w, ym - h2 / 2, 1, -1), cove(s, w, ym + h2 / 2, 1, 1),
            cove(s, w, CAP - p["h3"], 1, -1)]


@glyph("F")
def _(s, p):
    w = p["w"]
    ym, h2 = p["ym"], p["h2"]
    return [shape(s, stem(0, w, (SXT, 0), (SXB, p["sbr"]))),
            shape(s, hook(1, p["e1"], 0, 1, p["bh1"], p["f1"], p["h1"], p["r1"], p.get("rl1"))),
            shape(s, mid_arm(1, p["e2"], ym, h2, p["su"], p["sd"], p.get("fo", 0.0), p.get("mbw"), p.get("mbr"))),
            cove(s, w, p["h1"], 1, 1), cove(s, w, ym - h2 / 2, 1, -1), cove(s, w, ym + h2 / 2, 1, 1)]


@glyph("L")
def _(s, p):
    w = p["w"]
    return [shape(s, stem(0, w, (SXT, p["str"]), (SXB, 0))),
            shape(s, hook(1, p["e"], CAP, -1, p["bh"], p["f"], p["h"], p["r"], p.get("rl"))),
            cove(s, w, CAP - p["h"], 1, -1)]


def bowl(s, p):
    return minus(spline(s, p["o"]), spline(s, p["i"]))


def stem_bowl(s, p, bot):
    w = p["w"]
    i = [list(q) for q in p["i"]]
    i[0][0] = i[4][0] = w
    o = [list(q) for q in p["o"]]
    o[0][1] = 0.0
    if o[4][1] > CAP - 1:
        o[3][1] = o[4][1] = CAP
    q = dict(p, i=i, o=o)
    mt = (i[1][1] - i[0][1]) / (i[1][0] - w)
    mb = (i[3][1] - i[4][1]) / (i[3][0] - w)
    return [shape(s, stem(0, w, (SXT, 0), bot)), bowl(s, q), cove(s, w, i[0][1], 1, 1, m=mt),
            cove(s, w, i[4][1], 1, -1, m=mb)], q


@glyph("D")
def _(s, p):
    return stem_bowl(s, p, (SXB, 0))[0]


@glyph("P")
def _(s, p):
    return stem_bowl(s, p, (SXB, p["sbr"]))[0]


@glyph("R")
def _(s, p):
    leg = shape(s, stroke(p["lt"], p["lb"], p["wl"], p["wl"], p["ly"], CAP, bot=(0, p["tail"]), brk=p["tb"]))

    parts, q = stem_bowl(s, p, (SXB, p["sbr"]))
    return parts + [minus(leg, spline(s, q["i"]))]


@glyph("O")
def _(s, p):
    return [bowl(s, p)]


@glyph("C")
def _(s, p):
    return [spline(s, p["n"])]


@glyph("G")
def _(s, p):
    n = [list(q) for q in p["n"]]
    x0, x1 = n[8][0], n[5][0]
    n[6] = [x1, p["sy"] + 2, 270, 180, 0, 0]
    n[7] = [x0, p["sy"] + 2, 180, 90, 0, 0]
    spur = stroke((x0 + x1) / 2, (x0 + x1) / 2, x1 - x0, x1 - x0, p["sy"], n[8][1] - 2, top=(p["sl"], p["sr"]))
    return [spline(s, n), shape(s, spur)]


@glyph("S")
def _(s, p):
    return [spline(s, p["n"])]


@glyph("U")
def _(s, p):
    w, x1, wt = p["w"], p["x1"], p["wt"]
    o, i = [list(q) for q in p["o"]], [list(q) for q in p["i"]]
    o[0][0] = o[1][0] = 0.0
    o[3][0] = o[4][0] = x1 + wt
    i[0][0] = i[1][0] = x1
    i[3][0] = i[4][0] = w
    yo = o[1][1]
    return [shape(s, stem(0, w, p["st"], (0, 0), y1=yo)),
            shape(s, stem(x1, x1 + wt, p["tt"], (0, 0), y1=yo, brk=BRKT)), minus(spline(s, o), spline(s, i))]


def vee(s, l0, wl, r0, wr, xv, wv, vy, ltop=(0, 0), rtop=(0, 0), rbrk=None):
    a, b = xv - wv / 2, xv + wv / 2
    left = shape(s, stroke(l0 + wl / 2, a + wl / 2, wl, wl, 0, vy, top=ltop))
    right = shape(s, stroke(r0 - wr / 2, b - wr / 2, wr, wr, 0, vy, top=rtop, brk=rbrk))
    return (s.cut(left, (b, vy), edge_normal((r0, 0), (b, vy), -1), 0),
            s.cut(right, (a, vy), edge_normal((l0, 0), (a, vy), 1), 0))


@glyph("N")
def _(s, p):
    x0, w, x1, w1, sl, wd, vy, dl = (p[k] for k in ("x0", "w", "x1", "w1", "sl", "wd", "vy", "dl"))
    diag = shape(s, [(dl, -1), (dl + wd, -1), (dl + wd + sl * (vy + 1), vy), (dl + sl * (vy + 1), vy)])
    diag = s.clip(diag, x0, 0, x1 + w1, vy + 1)
    return [shape(s, stem(x0, x0 + w, (p["stl"], 0), p["sb"], y0=0, brk=BRKT)), diag,
            shape(s, stem(x1, x1 + w1, p["s1"], (0, 0), y1=p["y1"], brk=BRKT))]


@glyph("M")
def _(s, p):
    w, x3, w3 = p["w"], p["x3"], p["w3"]
    thick, thin = vee(s, p["dl"], p["wd"], p["x2"], p["w2"], p["xv"], p["wv"], p["vy"])
    return [shape(s, stem(0, w, (p["stl"], 0), p["sb"], brk=BRKT)), s.clip(thick, 0, -1, x3 + 1, CAP + 3),
            s.clip(thin, w - 1, -1, x3 + 1, CAP + 3), shape(s, stem(x3, x3 + w3, p["s3t"], p["s3b"]))]


@glyph("V")
def _(s, p):
    return list(vee(s, 0, p["wl"], p["xr"], p["wr"], p["xv"], p["wv"], p["vy"], p["stl"], p["str"], BRKT))


@glyph("A")
def _(s, p):
    xa, ya, aw, wl, sl, wr, sr = p["xa"], p["ya"], p["aw"], p["wl"], p["sl"], p["wr"], p["sr"]
    h = CAP - ya
    lt, rt = xa - aw / 2 + wl / 2, xa + aw / 2 - wr / 2
    left = shape(s, stroke(lt, lt - sl * h, wl, wl, ya, CAP, bot=p["sbl"], brk=BRKT))
    right = shape(s, stroke(rt, rt + sr * h, wr, wr, ya, CAP, bot=p["sbr"]))
    e0 = (xa - aw / 2, ya)
    e1 = (e0[0] - sl * h, CAP)
    right = s.cut(right, e0, edge_normal(e0, e1, 1), 0)
    r0 = (xa + aw / 2, ya)
    left = s.cut(left, r0, edge_normal(r0, (r0[0] + sr * h, CAP), -1), 0)
    yc, hc = p["yc"], p["hc"]
    xl = lt - sl * (yc - ya)
    xr = rt + sr * (yc - ya)
    return [left, right, s.fill(xl, yc - hc / 2, xr, yc + hc / 2)]


@glyph("Y")
def _(s, p):
    xl, wl, xr, wr, yj, xs, ws = (p[k] for k in ("xl", "wl", "xr", "wr", "yj", "xs", "ws"))
    yjr = p.get("yjr", yj)
    a, b = xs, xs + ws
    sl, srr = (a - xl + wl / 2) / yj, (xr + wr / 2 - b) / yjr
    yb = max(yj, yjr) + 2
    left = shape(s, stroke(xl, xl + sl * yb, wl, wl, 0, yb, top=p["stl"]))
    right = shape(s, stroke(xr, xr - srr * yb, wr, wr, 0, yb, top=p["str"], brk=BRKT))
    lo = (xl - wl / 2, 0)
    ro = (xr + wr / 2, 0)
    left = s.cut(left, (b, yjr), edge_normal(ro, (ro[0] - srr * yjr, yjr), -1), 0)
    right = s.cut(right, (a, yj), edge_normal(lo, (lo[0] + sl * yj, yj), 1), 0)
    return [left, right, shape(s, stem(a, b, (0, 0), p["sb"], y0=min(yj, yjr) - 1))]


@glyph("Q")
def _(s, p):
    o = PARAMS["O"]
    return [bowl(s, o), spline(s, p["n"])]


PARAMS["Q"] = dict(n=[[24.0, 44.8, 196, 23, 0, 5], [36.5, 49.57, 13, 13, 5, 5], [49.0, 51.81, 6.4, 71.5, 4, 0],
                      [49.1, 52.71, 71.5, 170.3, 0, 5], [32.0, 53.81, 180, 180, 7, 8],
                      [15.0, 46.85, 223.6, 223.6, 6, 3], [12.5, 43.8, 234, 16, 2, 0]])


def layer(ch, p=None):
    s = Skeleton(CAP, (1.0, 1.0), 1.0, U)
    return merge(BUILD[ch](s, PARAMS[ch] if p is None else p))


def contours(l):
    out = []
    for c in l:
        pts = list(c)
        k = next(i for i, q in enumerate(pts) if q.on_curve)
        pts = pts[k:] + pts[:k] + [pts[k]]
        g = lambda q: (q.x / U, CAP - q.y / U)
        segs, ctl = [g(pts[0])], []
        for q in pts[1:]:
            if q.on_curve:
                segs.append(("C", *ctl, g(q)) if ctl else ("L", g(q)))
                ctl = []
            else:
                ctl.append(g(q))
        out.append(segs)
    return out
