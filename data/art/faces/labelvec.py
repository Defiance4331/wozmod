from .buttonvec import U, chain, contours as _contours, oval, rounded
from .skeleton import Skeleton, merge

CAP = 17.1
B = CAP
STEM, BAR = 2.9790625, 2.9500000
PEN = (STEM, BAR)
OVER_T, OVER_B = -0.1656250, 17.4828125
X0 = STEM / 2

PARAMS = {
    'A': dict(xc=7.6, ah=0.1187, fh=7, yc=12.3688, ku=0.9875, kd=0.95),
    'D': dict(xd=13.4812, rx=3.4813, ry=6.5187, t=0.6188),
    'E': dict(e1=12.9313, ym=8.3187, e2=11.9562, e3=13.1813),
    'F': dict(e1=13.075, ym=8.69375, e2=12.2),
    'G': dict(w=15.6875, rx=4.7875, ry=5.50005, t=0.694375, yt=6.0687, yg=9.04995, xg=7.1, pv=3.123388,
        ph=2.901563, rxt=5.1625, ryt=5.83755, rxb=4.375, ryb=5.38755),
    'I': dict(),
    'L': dict(e=12.8687),
    'M': dict(w=17.2187, dt=1.6938, db=0.35, yv=16.95, ku=0.8603125, kd=0.8703125),
    'N': dict(xr=13.7625, dt=1.4062, db=1.2, k=0.9775),
    'O': dict(w=16.0875, rx=4.375, ry=7.45, t=0.76, pv=3.160888, ph=3.014063),
    'P': dict(xb=12.6375, yb=9.525, rx1=3.0625, ry1=3.5875, rx2=2.7437, ry2=5.0875, t=0.7),
    'R': dict(xb=12.6062, yb=9.0562, rx1=3.625, ry1=2.95, rx2=4.2625, ry2=3.6625, t=0.700625, l0=11.275,
        l1=13.5563, k=1.03),
    'S': dict(xl=1.4875, xr=12.675, xrb=13.1437, xlb=1.675, ya=7.8375, yb=9.0125, rxt=4.9812, ryt=3.3375,
        ri=2.4563, rxb=5.2062, ryb=2.9062, t=0.695, yt=5.325, ybt=11.4812),
    'T': dict(w=15.05),
    'U': dict(xr=13.425, rx=4.175, ry=3.5, t=0.58),
    'V': dict(xc=8.725, at=6.7063, bh=0.3062, ku=1.0425, kd=1.0225),
    'Y': dict(xm=9, ah=6.6, yj=10.825, ku=0.98375, kd=1.02125),
}

BEARINGS = {
    "A": (-0.08, 0.30), "D": (1.49, 1.48), "E": (1.49, 1.03), "F": (1.49, 0.78), "G": (1.29, 1.29),
    "I": (1.49, 1.70), "L": (1.49, 0.63), "M": (1.49, 1.70), "N": (1.49, 1.70), "O": (0.94, 1.14),
    "P": (1.49, 1.14), "R": (1.49, 0.68), "S": (0.14, 1.20), "T": (-0.21, 0.02), "U": (1.49, 1.70),
    "V": (0.31, 0.77), "Y": (-1.02, -1.02),
}
SPACE = 7.37


# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
ROWS = {
    "5a93503c1ee64215de74d5196e20c8da883582fea73bffde05b9aab9c85b034a": (+0.22, 7.45),
    "5ddcad9c51246326705eeab710470ffb4181b75a44e5769971ef05d61068e74d": (+0.46, 5.36),
    "0ec1bbbac7a32e191abfb8c624e8119030bb7cfad5bb52f6c4a6b6640bac7d7f": (-0.52, 7.42),
    "61852a9f98258e346aa2eb2bcdbf6fbb5fd82fa87b6850cc37794145a0571a3d": (-0.44, 7.22),
    "22b111c17b2c4be356ccf0448633388e32ca2ccd0d97148bcf2b91e09b710906": (-0.48, 7.37),
    "316a1d8733d73abafc8878c10395423ce5dc3f4aaa07a5191c231d3da726dfa5": (+0.12, 7.37),
    "d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e": (+0.26, 7.37),
}


# Keyed by word_key(word), the word's SHA-256, so the official word is not stored.
SETS = {
    "5a93503c1ee64215de74d5196e20c8da883582fea73bffde05b9aab9c85b034a": (0.0, 16.1298, 32.2219, 50.6363, 65.538, 90.6399, 110.9152, 130.1772, 148.6132),
    "5ddcad9c51246326705eeab710470ffb4181b75a44e5769971ef05d61068e74d": (0.0, 15.7694, 33.6331, 49.631, 71.6095, 91.9183, 111.2278, 129.6263),
    "61852a9f98258e346aa2eb2bcdbf6fbb5fd82fa87b6850cc37794145a0571a3d": (0.0, 17.3283, 41.127, 60.4676, 78.6801, 96.1125),
    "22b111c17b2c4be356ccf0448633388e32ca2ccd0d97148bcf2b91e09b710906": (0.0, 17.05, 34.6609, 50.1891, 68.5983, 73.1885, 90.052, 108.7296, 130.1229, 149.4548,
        167.736, 185.1371),
    "316a1d8733d73abafc8878c10395423ce5dc3f4aaa07a5191c231d3da726dfa5": (0.0, 17.025, 35.7305, 50.7386, 65.9084, 81.916, 97.0648),
    "0ec1bbbac7a32e191abfb8c624e8119030bb7cfad5bb52f6c4a6b6640bac7d7f": (0.0, 17.3363, 34.1793, 47.7948, 66.3823, 93.1321, 112.4579, 130.7329, 148.1391),
    "d0b1a869600d21076a3fa8b5f52546e55920588ce73dfb7e56ca9dc08bdcaf3e": (0.0, 19.3935, 34.6244, 51.7592, 57.9204, 76.3381, 93.3155),
}


def dpen(k):
    return (STEM * k, BAR * k)


def rpen():
    return PARAMS["O"]["pv"], PARAMS["O"]["ph"]


def rbox(w, pen):
    return pen[0] / 2, OVER_T + pen[1] / 2, w - pen[0] / 2, OVER_B - pen[1] / 2


BUILD = {}


def glyph(ch):
    def deco(fn):
        BUILD[ch] = fn
        return fn
    return deco


@glyph("E")
def _(s, p):
    return [s.vline(X0, 0, B), s.hline(BAR / 2, X0, p["e1"]), s.hline(p["ym"], X0, p["e2"]),
            s.hline(B - BAR / 2, X0, p["e3"])]


@glyph("F")
def _(s, p):
    return [s.vline(X0, 0, B), s.hline(BAR / 2, X0, p["e1"]), s.hline(p["ym"], X0, p["e2"])]


@glyph("I")
def _(s, p):
    return [s.vline(X0, 0, B)]


@glyph("L")
def _(s, p):
    return [s.vline(X0, 0, B), s.hline(B - BAR / 2, X0, p["e"])]


@glyph("T")
def _(s, p):
    return [s.hline(BAR / 2, 0, p["w"]), s.vline(p["w"] / 2, 0, B)]


@glyph("D")
def _(s, p):
    bowl = rounded([(X0, BAR / 2), (p["xd"], BAR / 2), (p["xd"], B - BAR / 2), (X0, B - BAR / 2)],
                   {1: (p["rx"], p["ry"], p["t"]), 2: (p["ry"], p["rx"], p["t"])})
    return [s.vline(X0, 0, B), chain(s, bowl)]


def bowl(p):
    return rounded([(X0, BAR / 2), (p["xb"], BAR / 2), (p["xb"], p["yb"]), (X0, p["yb"])],
                   {1: (p["rx1"], p["ry1"], p["t"]), 2: (p["ry2"], p["rx2"], p["t"])})


@glyph("P")
def _(s, p):
    return [s.vline(X0, 0, B), chain(s, bowl(p))]


@glyph("R")
def _(s, p):


    buried = p.get("buried", 0.3)
    y0 = p["yb"] - buried
    x0 = p["l0"] - buried * (p["l1"] - p["l0"]) / (B - p["yb"])
    return [s.vline(X0, 0, B), chain(s, bowl(p)),
            s.line((x0, y0), (p["l1"], B), pen=dpen(p["k"]), box=(-5, y0, 30, B))]


@glyph("O")
def _(s, p):
    pen = (p["pv"], p["ph"])
    x0, y0, x1, y1 = rbox(p["w"], pen)
    return [chain(s, oval([(x1, y1), (x1, y0), (x0, y0), (x0, y1)], p["rx"], p["ry"], p["t"]), closed=True,
                  pen=pen)]


@glyph("G")
def _(s, p):
    pv, ph = rpen()
    pen = (p.get("pv", pv), p.get("ph", ph))
    x0, y0, x1, y1 = rbox(p["w"], pen)
    rt = (p.get("rxt", p["rx"]), p.get("ryt", p["ry"]))
    rb = (p.get("rxb", p["rx"]), p.get("ryb", p["ry"]))
    t = p["t"]
    path = rounded([(x1, p["yt"]), (x1, y0), (x0, y0), (x0, y1), (x1, y1), (x1, p["yg"] - BAR / 2)],
                   {1: (rt[1], rt[0], t), 2: (rt[0], rt[1], t),
                    3: (rb[1], rb[0], t), 4: (rb[0], rb[1], t)})
    return [chain(s, path, pen=pen), s.hline(p["yg"], p["xg"], x1 + pen[0] / 2, pen=(pen[0], BAR))]


@glyph("S")
def _(s, p):
    pv, ph = rpen()
    pen = (p.get("pv", pv), p.get("ph", ph))
    top, bot, t = OVER_T + pen[1] / 2, OVER_B - pen[1] / 2, p["t"]
    path = rounded([(p["xr"], p["yt"]), (p["xr"], top), (p["xl"], top), (p["xl"], p["ya"]), (p["xrb"], p["yb"]),
                    (p["xrb"], bot), (p["xlb"], bot), (p["xlb"], p["ybt"])],
                   {1: (p["ryt"], p["rxt"], t), 2: (p["rxt"], p["ryt"], t), 3: (p["ri"], p["ri"], 0.552),
                    4: (p["ri"], p["ri"], 0.552), 5: (p["ryb"], p["rxb"], t), 6: (p["rxb"], p["ryb"], t)})
    return [chain(s, path, pen=pen)]


@glyph("U")
def _(s, p):
    yb = OVER_B - rpen()[1] / 2
    cup = rounded([(X0, -1), (X0, yb), (p["xr"], yb), (p["xr"], -1)],
                  {1: (p["ry"], p["rx"], p["t"]), 2: (p["rx"], p["ry"], p["t"])})
    return [chain(s, cup, pen=(STEM, rpen()[1]), box=(-5, 0, 30, 30))]


@glyph("M")
def _(s, p):
    x1, m = p["w"] - X0, p["w"] / 2
    box = (0, 0, p["w"], p["yv"])
    return [s.vline(X0, 0, B), s.vline(x1, 0, B),
            s.line((X0 + p["dt"], 0), (m - p["db"], p["yv"]), pen=dpen(p["kd"]), box=box),
            s.line((x1 - p["dt"], 0), (m + p["db"], p["yv"]), pen=dpen(p["ku"]), box=box)]


@glyph("N")
def _(s, p):
    return [s.vline(X0, 0, B), s.vline(p["xr"], 0, B),
            s.line((X0 + p["dt"], 0), (p["xr"] - p["db"], B), pen=dpen(p["k"]), box=(X0, 0, p["xr"], B))]


@glyph("A")
def _(s, p):
    L = lambda y: p["xc"] - p["ah"] + (p["ah"] - p["fh"]) * y / B
    R = lambda y: p["xc"] + p["ah"] - (p["ah"] - p["fh"]) * y / B
    box = (-5, 0, 30, B)
    return [s.line((L(0), 0), (L(B), B), pen=dpen(p["ku"]), box=box),
            s.line((R(0), 0), (R(B), B), pen=dpen(p["kd"]), box=box),
            s.hline(p["yc"], L(p["yc"]), R(p["yc"]))]


@glyph("V")
def _(s, p):
    c = p["xc"]
    return [s.line((c - p["at"], 0), (c - p["bh"], B), pen=dpen(p["kd"]), box=(-5, 0, 30, B)),
            s.line((c + p["at"], 0), (c + p["bh"], B), pen=dpen(p["ku"]), box=(-5, 0, 30, B))]


@glyph("Y")
def _(s, p):
    xm, ah, yj = p["xm"], p["ah"], p["yj"]
    return [s.line((xm - ah, 0), (xm, yj), pen=dpen(p["kd"]), box=(-5, 0, xm + STEM / 2, B)),
            s.line((xm + ah, 0), (xm, yj), pen=dpen(p["ku"]), box=(xm - STEM / 2, 0, 30, B)),
            s.vline(xm, yj - 1.0, B)]


def layer(ch, p=None):
    s = Skeleton(CAP, PEN, 1.0, U)
    return merge(BUILD[ch](s, PARAMS[ch] if p is None else p))


def contours(l):
    return _contours(l, CAP)
