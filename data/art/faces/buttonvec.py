import math

from .skeleton import Skeleton, merge

U = 50
DESIGN_CAP = 25.0
B = DESIGN_CAP
STEM, BAR = 2.31, 3.0
PEN = (STEM, BAR)
OVER_T, OVER_B = 0.0, 26.0
DK = 0.9525
DOT = BAR

AUTHORED = frozenset("JZ013456789.,'&():!?+")


PARAMS = {
    "A": dict(xc=7.1875, ah=0.1562, fh=6.0312, yc=16.4753, ku=0.9875, kd=0.9975),
    "B": dict(xb1=9.5, xb2=10.5063, ym=12.3458, rx1=3.3876, ry1=4.332, rx2=3.1437, ry2=4.7139, rx3=3.9063,
              ry3=5.7825, rx4=3.8937, ry4=4.4876, t=0.5195),
    "C": dict(w=14.2375, kx=1, ry=10.579, t=0.5775, ut=0.405, at=-1.0032, ub=0.3875, ab=1.0594),
    "D": dict(xd=11.475, rx=5.9813, ry=9.5859, t=0.547),
    "E": dict(e1=10.625, ym=12.5023, e2=9.3063, e3=10.6687),
    "F": dict(e1=10, ym=12.5037, e2=9),
    "G": dict(w=14.1813, kx=0.9438, ry=9.9262, t=0.5475, ut=0.3675, at=-0.9658, xs=11.9874, yk=20.7406,
              yg=13.5017, xg=6.8251),
    "H": dict(xr=11.0812, ym=12.4943),
    "I": dict(),
    "K": dict(xa=11.1563, ys=15.2352, u=0.69, xl=11.4563, ku=0.9965, kd=1.075),
    "L": dict(e=9.6811),
    "M": dict(w=15, dt=0.9063, db=0.0563, yv=24.9227, ku=0.9189, kd=0.917),
    "N": dict(xr=11.1312, dt=0.3188, db=0.6562, k=1.065),
    "O": dict(w=13.869, kx=1, ry=11.917, t=0.595, pv=2.269, ph=3.0062),
    "P": dict(xb=10.3625, yb=14.5562, rx1=4.3, ry1=5.8292, rx2=3.9437, ry2=5.7198, t=0.5445),
    "Q": dict(w=13.5125, ry=10.96, t=0.5725, u=0.775, a=1.0438, qb=28.3249, pv=2.2565, ph=2.963, ob=24.9926),
    "R": dict(xb=10.1626, yb=13.3455, rx1=3.5749, ry1=5.4851, rx2=3.8376, ry2=5.2007, t=0.5445, l0=6.8187,
              l1=11.0188),
    "S": dict(xl=1.2749, xr=10.1625, xrb=10.125, xlb=1.2188, ya=10.7632, yb=13.9971, rxt=4.1813, ryt=6.4895,
              ri=4.1938, rxb=5, ryb=4.8188, t=0.597, yt=5.6241, at=-0.8906, ybt=18.9696, ab=0.5907,
              pv=2.3065, ph=3.1624),
    "T": dict(w=11.8125),
    "U": dict(xr=10.5375, rx=4.3937, ry=6.9161, t=0.5695),
    "V": dict(xc=6.6313, at=5.7907, bh=0.3062, ku=1.0499, kd=1.102),
    "W": dict(xc=9.1812, ot=8.3875, bv=4.35, e=0.3563, at=0.3312, ya=0.6763, ku=0.9756, kd=0.9893),
    "X": dict(xc=6.3125, at=5.2438, bt=5.4124, ku=1.0131, kd=1.0655),
    "Y": dict(xm=6.1936, ah=5.3563, yj=16.0561, ku=0.9556, kd=1.0193),
    "2": dict(xl=1.2313, yt=7.8015, xr=9.6188, yd=9.4153, rx1=5.1813, ry1=5.7491, rx2=5.2375, ry2=5.4801,
              d3=1.8624, t=0.592, xb=-2.175, wb=10.7188),
    "-": dict(yc=12.4935, w=6.9375),
    "/": dict(xb=0.7874, xt=6.8688, d=0.5313, k=0.7925),

    "J": dict(xr=8.85, rx=4.2, ry=5.12, t=0.552, yh=16.91),
    "Z": dict(x0=0.5, xt=10.6, xb=11.0),
    "0": dict(w=11.0),
    "1": dict(xs=5.6, xf=0.6, yf=5.53),
    "3": dict(xl=1.13, yt=6.15, xu=9.0, ym=11.68, xm=4.4, xw=9.6, yb=19.47, ryu=4.71, rxu=4.0, rm=2.4, ryl=5.74,
              rxl=4.3, t=0.552),
    "4": dict(xs=8.4, yc=17.83, w=11.4),
    "5": dict(xs=1.6, xt=9.8, yj=9.84, xr=9.6, xl=1.13, yb=19.88, rx1=4.0, ry1=4.71, rx2=4.3, ry2=5.53, t=0.552),
    "6": dict(w=11.0, xh=9.6, yt=5.53, yb=9.84, ryh=6.15, rxh=4.3, rxb=4.3, ryb=6.15, t=0.552),
    "7": dict(w=10.8, xb=5.2),
    "8": dict(w=11.4, wu=10.0, ym=11.48, rxu=4.0, ryu=4.71, rxl=4.4, ryl=5.74, t=0.552),
    "9": dict(),
    ".": dict(w=2.6),
    ",": dict(t0=0.4, xt=-0.5, wt=1.1, d=3.6),
    "'": dict(e=0.55, h=7.17),
    ":": dict(y=6.76),
    "!": dict(e=0.4, yb=19.26),
    "?": dict(xl=1.13, yt=7.17, xr=9.0, ym=13.93, xc=5.0, ys=19.26, ry1=4.71, rx1=4.0, ry2=3.48, rx2=3.0, r4=2.2,
              t=0.552),
    "+": dict(w=10.0),
    "(": dict(w=6.0, yt=-1.64, yd=3.0, t=0.9),
    ")": dict(),
    "&": dict(xa=3.4, ya=9.22, xb=8.8, yb=6.66, yc=15.88, xw=7.0, xe=11.8, yw=13.32, xg=11.8, r1=2.5, ru=2.9,
              r4=2.5, r5=3.0, rxl=4.2, ryl=5.12, r7=3.5, t=0.552),
}


BEARINGS = {
    "A": (0.89, 0.89), "B": (2.68, 1.52), "C": (1.77, 1.90), "D": (2.68, 2.32), "E": (2.68, 2.03),
    "F": (2.68, 1.74), "G": (1.97, 2.21), "H": (2.68, 2.74), "I": (2.68, 2.74), "K": (2.68, 0.94),
    "L": (2.68, 1.76), "M": (2.68, 2.74), "N": (2.68, 2.74), "O": (2.11, 2.11), "P": (2.68, 1.82),
    "Q": (2.11, 2.15), "R": (2.68, 1.73), "S": (2.04, 2.42), "T": (1.20, 1.20), "U": (2.68, 2.74),
    "V": (0.82, 0.82), "W": (1.12, 1.12), "X": (0.94, 0.94), "Y": (0.32, 0.32), "-": (2.04, 2.04),
    "/": (3.95, 3.95),


    "J": (2.10, 2.74), "Z": (1.85, 1.85), "0": (2.10, 2.10), "1": (1.20, 2.74), "2": (2.00, 2.01),
    "3": (2.00, 2.41), "4": (1.70, 1.74), "5": (2.00, 2.41), "6": (2.10, 2.10), "7": (1.20, 1.20),
    "8": (2.10, 2.10), "9": (2.10, 2.10), ".": (2.68, 2.74), ",": (2.68, 2.74), "'": (2.68, 2.74),
    ":": (2.68, 2.74), "!": (2.68, 2.74), "?": (2.00, 2.20), "+": (2.00, 2.00), "(": (2.10, 0.92),
    ")": (0.92, 2.10), "&": (2.10, 0.92),
}
SPACE = 8.63


def rpen(p=None):
    p = PARAMS["O"] if p is None else p
    return p["pv"], p["ph"]


def dpen(k):
    return (STEM * DK * k, BAR * DK * k)


def ray(j, a, k):
    return j[0] + math.cos(a) * k, j[1] + math.sin(a) * k


def line_seg(a, b):
    return (a, (a[0] + (b[0] - a[0]) / 3, a[1] + (b[1] - a[1]) / 3), (a[0] + 2 * (b[0] - a[0]) / 3,
            a[1] + 2 * (b[1] - a[1]) / 3), b)


def rounded(pts, corners, closed=False):
    n = len(pts)
    d = {i: [c[0], c[1], c[2]] for i, c in corners.items()}
    for _ in range(3):
        for i in range(n if closed else n - 1):
            j = (i + 1) % n
            a = d[i][1] if i in d else 0.0
            b = d[j][0] if j in d else 0.0
            side = math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
            if a + b > side:
                if i in d:
                    d[i][1] *= side / (a + b)
                if j in d:
                    d[j][0] *= side / (a + b)

    def toward(v, q, k):
        L = math.hypot(q[0] - v[0], q[1] - v[1])
        return v[0] + (q[0] - v[0]) * k / L, v[1] + (q[1] - v[1]) * k / L

    order = list(range(1, n)) + [0] if closed else list(range(1, n - 1))
    cur = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2) if closed else pts[0]
    segs = []
    for i in order:
        v = pts[i]
        if i not in d or d[i][0] <= 0 or d[i][1] <= 0:
            segs.append(line_seg(cur, v))
            cur = v
            continue
        din, dout, tt = d[i]
        p1, p2 = toward(v, pts[i - 1], din), toward(v, pts[(i + 1) % n], dout)
        if math.hypot(p1[0] - cur[0], p1[1] - cur[1]) > 1e-6:
            segs.append(line_seg(cur, p1))
        segs.append((p1, (p1[0] + (v[0] - p1[0]) * tt, p1[1] + (v[1] - p1[1]) * tt),
                     (p2[0] + (v[0] - p2[0]) * tt, p2[1] + (v[1] - p2[1]) * tt), p2))
        cur = p2
    end = segs[0][0] if closed else pts[-1]
    if math.hypot(end[0] - cur[0], end[1] - cur[1]) > 1e-6:
        segs.append(line_seg(cur, end))
    return segs


def chain(s, segs, closed=False, **kw):
    return s.curve(segs[0][0], *[q for seg in segs for q in seg[1:]], closed=closed, **kw)


def round_box(w, pen=None, over=(OVER_T, OVER_B)):
    pen = pen or rpen()
    return pen[0] / 2, over[0] + pen[1] / 2, w - pen[0] / 2, over[1] - pen[1] / 2


def oval(pts, rx, ry, t):
    return rounded(pts, {1: (ry, rx, t), 2: (rx, ry, t), 3: (ry, rx, t), 0: (rx, ry, t)}, closed=True)


def rtops():
    return OVER_T + rpen()[1] / 2, OVER_B - rpen()[1] / 2


def edged(s, e1, e2, toward, pen, box):
    L = math.hypot(e2[0] - e1[0], e2[1] - e1[1])
    n = ((e1[1] - e2[1]) / L, (e2[0] - e1[0]) / L)
    if (toward[0] - e1[0]) * n[0] + (toward[1] - e1[1]) * n[1] < 0:
        n = (-n[0], -n[1])
    h = math.hypot(pen[0] / 2 * n[0], pen[1] / 2 * n[1])
    return s.line((e1[0] + n[0] * h, e1[1] + n[1] * h), (e2[0] + n[0] * h, e2[1] + n[1] * h), pen=pen, box=box)


def obox(p, w=None, pen=None, over=(OVER_T, OVER_B)):
    x0, y0, x1, y1 = box = round_box(p["w"] if w is None else w, pen, over)
    segs = oval([(x1, y1), (x1, y0), (x0, y0), (x0, y1)], p["kx"] * (x1 - x0) / 2, p["ry"], p["t"])
    tr = next(sg for sg in segs if sg[0][0] == x1 and abs(sg[3][1] - y0) < 1e-9)
    br = next(sg for sg in segs if abs(sg[0][1] - y1) < 1e-9 and sg[3][0] == x1 and sg[3][1] < y1)
    return segs, tr, tuple(reversed(br)), box


def bez(P, u):
    b = [(1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u * u * (1 - u), u ** 3]
    return sum(k * p[0] for k, p in zip(b, P)), sum(k * p[1] for k, p in zip(b, P))


def sub(P, u0, u1):
    def split(P, u):
        lerp = lambda a, b: (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
        a, b, c = lerp(P[0], P[1]), lerp(P[1], P[2]), lerp(P[2], P[3])
        d, e = lerp(a, b), lerp(b, c)
        f = lerp(d, e)
        return [P[0], a, d, f], [f, e, c, P[3]]
    right = split(P, u0)[1]
    return split(right, (u1 - u0) / (1 - u0))[0] if u0 < 1 else right


def cut_quad(j, a, toward, depth=3.5):
    n = (-math.sin(a), math.cos(a))
    if (toward[0] - j[0]) * n[0] + (toward[1] - j[1]) * n[1] < 0:
        n = (-n[0], -n[1])
    p, q = ray(j, a, -2.5), ray(j, a, 6)
    return [p, q, (q[0] + n[0] * depth, q[1] + n[1] * depth), (p[0] + n[0] * depth, p[1] + n[1] * depth)]


def terminal(x, y, a, down):
    yl, yr = y - 2.5 * math.tan(a), y + 2.5 * math.tan(a)
    far = y + down * 4.0
    return [(x - 2.5, yl), (x + 2.5, yr), (x + 2.5, far), (x - 2.5, far)]


BUILD = {}


def glyph(ch):
    def deco(fn):
        BUILD[ch] = fn
        return fn
    return deco


X0 = STEM / 2


@glyph("E")
def _(s, p):
    return [s.vline(X0, 0, B), s.hline(BAR / 2, X0, p["e1"]), s.hline(p["ym"], X0, p["e2"]),
            s.hline(B - BAR / 2, X0, p["e3"])]


@glyph("F")
def _(s, p):
    return [s.vline(X0, 0, B), s.hline(BAR / 2, X0, p["e1"]), s.hline(p["ym"], X0, p["e2"])]


@glyph("H")
def _(s, p):
    return [s.vline(X0, 0, B), s.vline(p["xr"], 0, B), s.hline(p["ym"], X0, p["xr"])]


@glyph("I")
def _(s, p):
    return [s.vline(X0, 0, B)]


@glyph("L")
def _(s, p):
    return [s.vline(X0, 0, B), s.hline(B - BAR / 2, X0, p["e"])]


@glyph("T")
def _(s, p):
    return [s.hline(BAR / 2, 0, p["w"]), s.vline(p["w"] / 2, 0, B)]


@glyph("B")
def _(s, p):
    up = rounded([(X0, BAR / 2), (p["xb1"], BAR / 2), (p["xb1"], p["ym"]), (X0, p["ym"])],
                 {1: (p["rx1"], p["ry1"], p["t"]), 2: (p["ry2"], p["rx2"], p["t"])})
    lo = rounded([(X0, p["ym"]), (p["xb2"], p["ym"]), (p["xb2"], B - BAR / 2), (X0, B - BAR / 2)],
                 {1: (p["rx3"], p["ry3"], p["t"]), 2: (p["ry4"], p["rx4"], p["t"])})
    return [s.vline(X0, 0, B), chain(s, up), chain(s, lo)]


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
    return [s.vline(X0, 0, B), chain(s, bowl(p)), s.line((p["l0"], p["yb"]), (p["l1"], B), box=(-5, p["yb"], 30, B))]


@glyph("K")
def _(s, p):
    pa, pl = dpen(p["ku"]), dpen(p["kd"])
    a0, a1 = (p["xa"], 0), (X0, p["ys"])
    j = (a0[0] + (a1[0] - a0[0]) * p["u"], a0[1] + (a1[1] - a0[1]) * p["u"])
    L = math.hypot(a1[0] - a0[0], a1[1] - a0[1])
    n = ((a0[1] - a1[1]) / L, (a1[0] - a0[0]) / L)
    leg = s.cut(s.line(j, (p["xl"], B), pen=pl, box=(X0, 0, 30, B)), j, n, 0.0)
    return [s.vline(X0, 0, B), s.line(a0, a1, pen=pa, box=(X0, 0, 30, B)), leg]


@glyph("M")
def _(s, p):
    x1 = p["w"] - X0
    m = p["w"] / 2
    box = (0, 0, p["w"], p["yv"])
    return [s.vline(X0, 0, B), s.vline(x1, 0, B),
            s.line((X0 + p["dt"], 0), (m - p["db"], p["yv"]), pen=dpen(p["kd"]), box=box),
            s.line((x1 - p["dt"], 0), (m + p["db"], p["yv"]), pen=dpen(p["ku"]), box=box)]


@glyph("N")
def _(s, p):
    return [s.vline(X0, 0, B), s.vline(p["xr"], 0, B),
            s.line((X0 + p["dt"], 0), (p["xr"] - p["db"], B), pen=dpen(p["k"]), box=(X0, 0, p["xr"], B))]


@glyph("V")
def _(s, p):
    c = p["xc"]
    return [s.line((c - p["at"], 0), (c - p["bh"], B), pen=dpen(p["kd"]), box=(-5, 0, 30, B)),
            s.line((c + p["at"], 0), (c + p["bh"], B), pen=dpen(p["ku"]), box=(-5, 0, 30, B))]


@glyph("W")
def _(s, p):
    c, out = p["xc"], []
    for k, outer, inner in ((-1, p["kd"], p["ku"]), (1, p["ku"], p["kd"])):
        out += [s.line((c + k * p["ot"], 0), (c + k * (p["bv"] + p["e"]), B), pen=dpen(outer), box=(-5, 0, 40, B)),
                s.line((c + k * p["at"], p["ya"]), (c + k * (p["bv"] - p["e"]), B), pen=dpen(inner),
                       box=(-5, p["ya"], 40, B))]
    return out


@glyph("X")
def _(s, p):
    c = p["xc"]
    return [s.line((c - p["at"], 0), (c + p["bt"], B), pen=dpen(p["kd"]), box=(-5, 0, 30, B)),
            s.line((c + p["at"], 0), (c - p["bt"], B), pen=dpen(p["ku"]), box=(-5, 0, 30, B))]


@glyph("Y")
def _(s, p):
    xm, ah, yj = p["xm"], p["ah"], p["yj"]
    return [s.line((xm - ah, 0), (xm, yj), pen=dpen(p["kd"]), box=(-5, 0, xm + STEM / 2, B)),
            s.line((xm + ah, 0), (xm, yj), pen=dpen(p["ku"]), box=(xm - STEM / 2, 0, 30, B)),
            s.vline(xm, yj - 1.0, B)]


@glyph("A")
def _(s, p):
    L = lambda y: p["xc"] - p["ah"] + (p["ah"] - p["fh"]) * y / B
    R = lambda y: p["xc"] + p["ah"] - (p["ah"] - p["fh"]) * y / B
    box = (-5, 0, 30, B)
    return [s.line((L(0), 0), (L(B), B), pen=dpen(p["ku"]), box=box),
            s.line((R(0), 0), (R(B), B), pen=dpen(p["kd"]), box=box),
            s.hline(p["yc"], L(p["yc"]), R(p["yc"]))]


@glyph("O")
def _(s, p):
    pen = rpen(p)
    return [chain(s, obox(p, pen=pen)[0], closed=True, pen=pen)]


@glyph("Q")
def _(s, p):
    pen = (p["pv"], p["ph"])
    segs, _, br, _ = obox(dict(PARAMS["O"], w=p["w"], ry=p["ry"], t=p["t"]), pen=pen, over=(OVER_T, p["ob"]))
    q = bez(br, p["u"])
    return [chain(s, segs, closed=True, pen=pen),
            s.stroke([q, ray(q, p["a"], 12)], pen=s.diag(), box=(-5, -5, 30, p["qb"]))]


@glyph("C")
def _(s, p):
    segs, tr, br, _ = obox(p)
    jt, jb = bez(tr, p["ut"]), bez(br, p["ub"])
    o = chain(s, segs, closed=True, pen=rpen())
    return [s.remove(o, [ray(jt, p["at"], -2.5), ray(jt, p["at"], 6), ray(jb, p["ab"], 6), ray(jb, p["ab"], -2.5)])]


@glyph("G")
def _(s, p):
    _, tr, _, (x0, y0, x1, y1) = obox(p)
    rx, ry, t = p["kx"] * (x1 - x0) / 2, p["ry"], p["t"]


    xs, ys = p["xs"], p["yg"]
    arc = sub(tr, max(0.0, p["ut"] - 0.12), 1)
    rest = rounded([arc[3], (x0, y0), (x0, y1), (xs, y1), (xs, ys)],
                   {1: (rx, ry, t), 2: (ry, rx, t), 3: (rx, y1 - p["yk"], t)})
    g = chain(s, [tuple(arc)] + rest, pen=rpen())
    return [s.remove(g, cut_quad(bez(tr, p["ut"]), p["at"], arc[0])),
            s.hline(p["yg"], p["xg"], xs + rpen()[0] / 2, pen=(rpen()[0], BAR))]


@glyph("S")
def _(s, p):
    pen = (p["pv"], p["ph"])
    top, bot = OVER_T + pen[1] / 2, OVER_B - pen[1] / 2
    t = p["t"]
    segs = rounded([(p["xr"], p["yt"] + 2.5), (p["xr"], top), (p["xl"], top), (p["xl"], p["ya"]),
                    (p["xrb"], p["yb"]), (p["xrb"], bot), (p["xlb"], bot), (p["xlb"], p["ybt"] - 2.5)],
                   {1: (p["ryt"], p["rxt"], t), 2: (p["rxt"], p["ryt"], t), 3: (p["ri"], p["ri"], 0.552),
                    4: (p["ri"], p["ri"], 0.552), 5: (p["ryb"], p["rxb"], t), 6: (p["rxb"], p["ryb"], t)})
    l = chain(s, segs, pen=pen)
    return [s.remove(s.remove(l, terminal(p["xr"], p["yt"], p["at"], 1)), terminal(p["xlb"], p["ybt"], p["ab"], -1))]


def cup(p, top):
    yb = OVER_B - rpen()[1] / 2
    return rounded([(X0, top), (X0, yb), (p["xr"], yb), (p["xr"], -1)],
                   {1: (p["ry"], p["rx"], p["t"]), 2: (p["rx"], p["ry"], p["t"])})


@glyph("U")
def _(s, p):
    return [chain(s, cup(p, -1), pen=(STEM, rpen()[1]), box=(-5, 0, 30, 30))]


@glyph("2")
def _(s, p):
    top, yb, t = OVER_T + rpen()[1] / 2, B - BAR / 2, p["t"]
    segs = rounded([(p["xl"], p["yt"]), (p["xl"], top), (p["xr"], top), (p["xr"], p["yd"]), (p["xb"], yb)],
                   {1: (p["ry1"], p["rx1"], t), 2: (p["rx2"], p["ry2"], t), 3: (p["d3"], p["d3"], 0.552)})
    return [chain(s, segs, pen=rpen(), box=(0, -5, 30, yb)), s.hline(yb, 0, p["wb"])]


@glyph("-")
def _(s, p):
    return [s.hline(p["yc"], 0, p["w"])]


@glyph("/")
def _(s, p):
    k = DK * p["k"]
    return [s.line((p["xb"], B), (p["xt"], 0), ext=p["d"] + 2, pen=(STEM * k, BAR * k),
                   box=(-5, -p["d"], 30, B + p["d"]))]


@glyph("J")
def _(s, p):
    return [chain(s, cup(p, p["yh"]), pen=(STEM, rpen()[1]), box=(-5, 0, 30, 30))]


@glyph("Z")
def _(s, p):
    return [s.hline(BAR / 2, p["x0"], p["xt"]), s.hline(B - BAR / 2, 0, p["xb"]),
            s.aimed((p["xt"], BAR), (0, B - BAR), s.diag(), (0, BAR / 2, p["xt"], B - BAR / 2))]


@glyph("0")
def _(s, p):
    return [chain(s, obox(dict(PARAMS["O"], w=p["w"]))[0], closed=True, pen=rpen())]


@glyph("1")
def _(s, p):
    return [s.vline(p["xs"], 0, B), s.line((p["xs"], 0), (p["xf"], p["yf"]), box=(0, 0, p["xs"], B))]


@glyph("3")
def _(s, p):
    pen, t, (top, bot), xl, S = rpen(), p["t"], rtops(), p["xl"], PARAMS["S"]
    up = rounded([(xl, p["yt"] + 2.5), (xl, top), (p["xu"], top), (p["xu"], p["ym"]), (p["xm"], p["ym"])],
                 {1: (p["ryu"], p["rxu"], t), 2: (p["rxu"], p["ryu"], t), 3: (p["rm"], p["rm"], t)})
    lo = rounded([(p["xm"], p["ym"]), (p["xw"], p["ym"]), (p["xw"], bot), (xl, bot), (xl, p["yb"] - 2.5)],
                 {1: (p["rm"], p["rm"], t), 2: (p["ryl"], p["rxl"], t), 3: (p["rxl"], p["ryl"], t)})
    return [s.remove(chain(s, up, pen=pen), terminal(xl, p["yt"], -S["at"], 1)),
            s.remove(chain(s, lo, pen=pen), terminal(xl, p["yb"], S["ab"], -1))]


@glyph("4")
def _(s, p):
    xs, yc, pen = p["xs"], p["yc"], s.diag()
    e2, xe = (0, yc - BAR / 2), xs - STEM / 2 - 3.0
    for _ in range(12):
        L = math.hypot(xe, e2[1])
        n = (e2[1] / L, xe / L)
        xe = xs - STEM / 2 - 2 * math.hypot(pen[0] / 2 * n[0], pen[1] / 2 * n[1]) / n[0]
    return [s.vline(xs, 0, B), s.hline(yc, 0, p["w"]),
            edged(s, (xe, 0), e2, (xs, yc), pen, (0, 0, xs, yc + BAR / 2))]


@glyph("5")
def _(s, p):
    pen, t, (_, bot), xs, yj = rpen(), p["t"], rtops(), p["xs"], p["yj"]
    bowl = rounded([(xs, yj), (p["xr"], yj), (p["xr"], bot), (p["xl"], bot), (p["xl"], p["yb"] - 2.5)],
                   {1: (p["rx1"], p["ry1"], t), 2: (p["ry2"], p["rx2"], t), 3: (p["rx2"], p["ry2"], t)})
    return [s.hline(BAR / 2, xs - STEM / 2, p["xt"]), s.vline(xs, 0, yj + pen[1] / 2),
            s.remove(chain(s, bowl, pen=pen), terminal(p["xl"], p["yb"], PARAMS["S"]["ab"], -1))]


def six(s, f):
    p, pen, (top, bot) = PARAMS["6"], rpen(), rtops()
    x0, x1, t = pen[0] / 2, p["w"] - pen[0] / 2, p["t"]
    hook = rounded([f(p["xh"], p["yt"] + 2.5), f(p["xh"], top), f(x0, top), f(x0, (p["yb"] + bot) / 2)],
                   {1: (p["ryh"], p["rxh"], t), 2: (p["rxh"], p["ryh"], t)})
    cut = [f(*q) for q in terminal(p["xh"], p["yt"], PARAMS["S"]["at"], 1)]
    bowl = oval([f(x1, bot), f(x1, p["yb"]), f(x0, p["yb"]), f(x0, bot)], p["rxb"], p["ryb"], t)
    return [s.remove(chain(s, hook, pen=pen), cut), chain(s, bowl, closed=True, pen=pen)]


@glyph("6")
def _(s, p):
    return six(s, lambda x, y: (x, y))


@glyph("9")
def _(s, p):
    return six(s, lambda x, y: (PARAMS["6"]["w"] - x, OVER_T + OVER_B - y))


@glyph("7")
def _(s, p):
    return [s.hline(BAR / 2, 0, p["w"]), edged(s, (p["w"], BAR), (p["xb"], B), (0, B), s.diag(), (-5, 0, p["w"], B))]


@glyph("8")
def _(s, p):
    pen, t, (top, bot), c = rpen(), p["t"], rtops(), p["w"] / 2
    hu, hl = p["wu"] / 2 - pen[0] / 2, p["w"] / 2 - pen[0] / 2
    up = oval([(c + hu, p["ym"]), (c + hu, top), (c - hu, top), (c - hu, p["ym"])], p["rxu"], p["ryu"], t)
    lo = oval([(c + hl, bot), (c + hl, p["ym"]), (c - hl, p["ym"]), (c - hl, bot)], p["rxl"], p["ryl"], t)
    return [chain(s, up, closed=True, pen=pen), chain(s, lo, closed=True, pen=pen)]


@glyph(".")
def _(s, p):
    return [s.fill(0, B - DOT, p["w"], B)]


@glyph(",")
def _(s, p):
    w = PARAMS["."]["w"]
    return [s.poly([(0, B - DOT), (w, B - DOT), (w, B + p["t0"]), (p["xt"] + p["wt"], B + p["d"]),
                    (p["xt"], B + p["d"]), (0, B)])]


@glyph("'")
def _(s, p):
    w = PARAMS["."]["w"]
    return [s.poly([(0, 0), (w, 0), (w - p["e"], p["h"]), (p["e"], p["h"])])]


@glyph(":")
def _(s, p):
    w = PARAMS["."]["w"]
    return [s.fill(0, p["y"], w, p["y"] + DOT), s.fill(0, B - DOT, w, B)]


@glyph("!")
def _(s, p):
    w = PARAMS["."]["w"]
    return [s.poly([(0, 0), (w, 0), (w - p["e"], p["yb"]), (p["e"], p["yb"])]), s.fill(0, B - DOT, w, B)]


@glyph("?")
def _(s, p):
    pen, t, (top, _), xc, w = rpen(), p["t"], rtops(), p["xc"], PARAMS["."]["w"]
    path = rounded([(p["xl"], p["yt"]), (p["xl"], top), (p["xr"], top), (p["xr"], p["ym"]), (xc, p["ym"]),
                    (xc, p["ys"])],
                   {1: (p["ry1"], p["rx1"], t), 2: (p["rx1"], p["ry1"], t), 3: (p["ry2"], p["rx2"], t),
                    4: (p["r4"], p["r4"], 0.552)})
    return [chain(s, path, pen=pen), s.fill(xc - w / 2, B - DOT, xc + w / 2, B)]


@glyph("+")
def _(s, p):
    w, yc = p["w"], PARAMS["-"]["yc"]
    return [s.hline(yc, 0, w), s.vline(w / 2, yc - w / 2, yc + w / 2)]


def paren(s, f):
    p, pen = PARAMS["("], rpen()
    x1, yt, yb = p["w"] - pen[0] / 2, p["yt"], B + p["yd"]
    xv = x1 - (x1 - pen[0] / 2) / (0.75 * p["t"])
    v, a, b = (xv, (yt + yb) / 2), (x1 + 1.0, yt - 2.0), (x1 + 1.0, yb + 2.0)
    c1 = (a[0] + (v[0] - a[0]) * p["t"], a[1] + (v[1] - a[1]) * p["t"])
    c2 = (b[0] + (v[0] - b[0]) * p["t"], b[1] + (v[1] - b[1]) * p["t"])
    return [s.curve(f(*a), f(*c1), f(*c2), f(*b), pen=pen, box=(-5, yt, 30, yb))]


@glyph("(")
def _(s, p):
    return paren(s, lambda x, y: (x, y))


@glyph(")")
def _(s, p):
    return paren(s, lambda x, y: (PARAMS["("]["w"] - x, y))


@glyph("&")
def _(s, p):
    pen, t, (top, bot), x0 = rpen(), p["t"], rtops(), rpen()[0] / 2
    xa, ya, xg = p["xa"], p["ya"], p["xg"]
    start = (xg + (xg - xa) * 0.2, B + (B - ya) * 0.2)
    arm = (p["xw"] + (p["xe"] - p["xw"]) * 1.3, bot + (p["yw"] - bot) * 1.3)
    pts = [start, (xa, ya), (xa, top), (p["xb"], top), (p["xb"], p["yb"]), (x0, p["yc"]), (x0, bot),
           (p["xw"], bot), arm]
    corners = {1: (p["r1"], p["r1"], t), 2: (p["ru"], p["ru"], t), 3: (p["ru"], p["ru"], t),
               4: (p["r4"], p["r4"], t), 5: (p["r5"], p["r5"], t), 6: (p["ryl"], p["rxl"], t),
               7: (p["r7"], p["r7"], t)}
    l = chain(s, rounded(pts, corners), pen=pen)
    l = s.remove(l, [(p["xw"] + 1.5, B), (30, B), (30, B + 5), (p["xw"] + 1.5, B + 5)])
    xc = p["xb"] + pen[0] / 2 + 0.05
    return [s.remove(l, [(xc, p["yw"] - 12), (30, p["yw"] - 12), (30, p["yw"]),
                         (xc, p["yw"])])]


def layer(ch, cap=DESIGN_CAP, p=None):
    s = Skeleton(cap, PEN, DK, U, band(cap))
    return merge(BUILD[ch](s, PARAMS[ch] if p is None else p))


def band(cap):
    if cap == DESIGN_CAP:
        return None
    d, lo, hi = cap - DESIGN_CAP, BAR, DESIGN_CAP - BAR

    def ymap(y):
        return y if y <= lo else y + d if y >= hi else y + d * (y - lo) / (hi - lo)
    return ymap


def contours(l, cap=DESIGN_CAP):
    out = []
    for c in l:
        pts = list(c)
        k = next(i for i, q in enumerate(pts) if q.on_curve)
        pts = pts[k:] + pts[:k] + [pts[k]]
        g = lambda q: (q.x / U, cap - q.y / U)
        segs, ctl = [g(pts[0])], []
        for q in pts[1:]:
            if q.on_curve:
                segs.append(("C", *ctl, g(q)) if ctl else ("L", g(q)))
                ctl = []
            else:
                ctl.append(g(q))
        out.append(segs)
    return out


STYLE_METRICS = {
    '493.9': dict(STEM=2.2725, BAR=3.2375, pv=2.219, ph=3.2687),
    '508.0': dict(STEM=2.2475, BAR=2.9875, pv=2.2315, ph=3.0437),
}


def styled_layer(ch, cap=DESIGN_CAP, style='501.3'):
    if style not in STYLE_METRICS:
        return layer(ch, cap)
    global STEM, BAR, PEN, X0
    old = STEM, BAR, PEN, X0, PARAMS['O']['pv'], PARAMS['O']['ph']
    metrics = STYLE_METRICS[style]
    try:
        STEM, BAR = metrics['STEM'], metrics['BAR']
        PEN, X0 = (STEM, BAR), STEM / 2
        PARAMS['O']['pv'], PARAMS['O']['ph'] = metrics['pv'], metrics['ph']
        return layer(ch, cap)
    finally:
        STEM, BAR, PEN, X0, PARAMS['O']['pv'], PARAMS['O']['ph'] = old


REF_VARIANTS = {'493.9': {'slant': 0.0, 'weight_x': 1.00640625, 'weight_y': 1.055},
 '501.3': {'slant': 0.0, 'weight_x': 0.99828125, 'weight_y': 1.0009375},
 '508.0': {'slant': 0.0, 'weight_x': 0.99703125, 'weight_y': 1.0},
 '508.35': {'slant': 0.0015000000000000002, 'weight_x': 0.974375, 'weight_y': 1.0},
 '508.40': {'slant': 0.0, 'weight_x': 0.999375, 'weight_y': 1.0025}}


def ref_layer(ch, ref, cap=DESIGN_CAP):
    variant = REF_VARIANTS[ref]
    global PARAMS, STEM, BAR, PEN, X0
    old = PARAMS, STEM, BAR, PEN, X0
    try:
        wx, wy = variant['weight_x'], variant['weight_y']
        PARAMS = {letter: dict(values) for letter, values in PARAMS.items()}
        for values in PARAMS.values():
            if 'pv' in values:
                values['pv'] *= wx
            if 'ph' in values:
                values['ph'] *= wy
        STEM, BAR = STEM * wx, BAR * wy
        PEN, X0 = (STEM, BAR), STEM / 2
        outline = layer(ch, cap)
        if variant['slant']:
            outline.transform((1., 0., variant['slant'], 1., 0., 0.))
        return outline
    finally:
        PARAMS, STEM, BAR, PEN, X0 = old
