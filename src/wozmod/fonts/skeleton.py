"""Skeleton rendering support."""
import math

import fontforge


def _layer(*contours):
    l = fontforge.layer()
    for c in contours:
        l += c
    return l


class Skeleton:
    def __init__(self, base, pen, dk=1.0, u=50, ymap=None):
        self.base = base
        self.pen = pen
        self.dk = dk
        self.u = u
        self.ymap = ymap

    def fp(self, x, y):
        if self.ymap:
            y = self.ymap(y)
        return (x * self.u, (self.base - y) * self.u)

    def diag(self):
        return (self.pen[0] * self.dk, self.pen[1] * self.dk)

    def contour(self, pts, radii, closed):

        n = len(pts)
        if not isinstance(radii, (list, tuple)):
            radii = [radii] * n
        out = []

        def fillet(i):
            vx, vy = pts[i]
            ax, ay = pts[i - 1]
            bx, by = pts[(i + 1) % n]
            r = radii[i]
            l1, l2 = math.hypot(ax - vx, ay - vy), math.hypot(bx - vx, by - vy)
            u1 = ((ax - vx) / l1, (ay - vy) / l1)
            u2 = ((bx - vx) / l2, (by - vy) / l2)
            theta = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
            if r <= 0 or theta < 1e-3 or abs(theta - math.pi) < 1e-3:
                return None
            d = r / math.tan(theta / 2)
            h = 4 / 3 * math.tan((math.pi - theta) / 4) * r
            p1 = (vx + u1[0] * d, vy + u1[1] * d)
            p2 = (vx + u2[0] * d, vy + u2[1] * d)
            return p1, (p1[0] - u1[0] * h, p1[1] - u1[1] * h), (p2[0] - u2[0] * h, p2[1] - u2[1] * h), p2

        def emit(i):
            f = fillet(i)
            if f is None:
                out.append((*pts[i], True))
            else:
                p1, c1, c2, p2 = f
                out.extend([(*p1, True), (*c1, False), (*c2, False), (*p2, True)])

        if closed:
            f0 = fillet(0)
            out.append((*(f0[3] if f0 else pts[0]), True))
            for i in range(1, n):
                emit(i)
            if f0:
                out.extend([(*f0[0], True), (*f0[1], False), (*f0[2], False)])
        else:
            out.append((*pts[0], True))
            for i in range(1, n - 1):
                emit(i)
            out.append((*pts[-1], True))
        c = fontforge.contour()
        for x, y, on in out:
            c += fontforge.point(*self.fp(x, y), on)
        c.closed = closed
        return c

    def rect(self, x0, y0, x1, y1):
        return self.contour([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0, True)

    def clip(self, l, x0=-5, y0=-5, x1=25, y1=30):

        l.removeOverlap()
        l.correctDirection()
        r = _layer(self.rect(x0, y0, x1, y1))
        r.correctDirection()
        for c in r:
            l += c
        l.intersect()
        return l

    def stroke(self, pts, r=0, closed=False, pen=None, box=None):
        pen = pen or self.pen
        l = _layer(self.contour(pts, r, closed))
        l.stroke('elliptical', pen[0] * self.u, pen[1] * self.u, pen[2] if len(pen) > 2 else 0, 'butt', 'miter')
        l.correctDirection()
        return self.clip(l, *box) if box else l

    def fill(self, x0, y0, x1, y1):
        l = _layer(self.rect(x0, y0, x1, y1))
        l.correctDirection()
        return l

    def poly(self, pts):
        l = _layer(self.contour(pts, 0, True))
        l.correctDirection()
        return l

    def remove(self, l, pts):

        p = self.poly(pts)
        p.exclude(l)
        return p

    def curve(self, p0, *cps, pen=None, box=None, closed=False):

        pen = pen or self.pen
        c = fontforge.contour()
        c.moveTo(*self.fp(*p0))
        for i in range(0, len(cps), 3):
            c.cubicTo(*(self.fp(*q) for q in cps[i:i + 3]))
        c.closed = closed
        l = _layer(c)
        l.stroke('elliptical', pen[0] * self.u, pen[1] * self.u, pen[2] if len(pen) > 2 else 0, 'butt', 'miter')
        l.correctDirection()
        return self.clip(l, *box) if box else l

    def line(self, a, b, ext=2.0, **kw):

        kw.setdefault('pen', self.diag())
        (ax, ay), (bx, by) = a, b
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / L, (by - ay) / L
        return self.stroke([(ax - ux * ext, ay - uy * ext), (bx + ux * ext, by + uy * ext)], **kw)

    def cut(self, l, j, n, d):

        qx, qy = j[0] + n[0] * d, j[1] + n[1] * d
        tx, ty = -n[1] * 60, n[0] * 60
        r = _layer(self.contour([(qx + tx, qy + ty), (qx - tx, qy - ty), (qx - tx - n[0] * 60, qy - ty - n[1] * 60),
                                 (qx + tx - n[0] * 60, qy + ty - n[1] * 60)], 0, True))
        r.correctDirection()
        l.removeOverlap()
        l.correctDirection()
        for c in r:
            l += c
        l.intersect()
        return l

    def kjoin(self, top, j, bot, stem_x, box, pen=None, leg_pen=None):


        pen = pen or self.diag()
        leg_pen = leg_pen or pen
        reach = lambda n, q: math.hypot(q[0] / 2 * n[0], q[1] / 2 * n[1])
        (tx, ty), (jx, jy), (bx, by) = top, j, bot
        L = math.hypot(tx - jx, ty - jy); v = ((tx - jx) / L, (ty - jy) / L)
        L = math.hypot(bx - jx, by - jy); u = ((bx - jx) / L, (by - jy) / L)
        m = (v[1], -v[0])
        n = (-u[1], u[0])
        sa = (stem_x - jx) / v[0]
        sl = (stem_x - jx) / u[0]
        arm = self.stroke([(tx + v[0] * 2, ty + v[1] * 2), (jx + v[0] * sa, jy + v[1] * sa)], pen=pen, box=box)
        leg = self.stroke([(jx + u[0] * sl, jy + u[1] * sl), (bx + u[0] * 2, by + u[1] * 2)], pen=leg_pen, box=box)
        return [self.cut(arm, j, n, reach(n, leg_pen)), self.cut(leg, j, m, reach(m, pen))]

    def aimed(self, c1, c2, pen, box):


        a, b = pen[0] / 2, pen[1] / 2
        m = ((c1[0] + c2[0]) / 2, (c1[1] + c2[1]) / 2)
        L, t = math.hypot(c1[0] - m[0], c1[1] - m[1]), math.atan2(c1[1] - m[1], c1[0] - m[0])
        phi = t
        for _ in range(20):
            phi = t - math.asin(math.hypot(a * math.sin(phi), b * math.cos(phi)) / L)
        d = (math.cos(phi) * 20, math.sin(phi) * 20)
        return self.line((m[0] + d[0], m[1] + d[1]), (m[0] - d[0], m[1] - d[1]), ext=0.0, pen=pen, box=box)

    def vline(self, x, y0, y1, **kw):
        return self.stroke([(x, y0), (x, y1)], **kw)

    def hline(self, y, x0, x1, **kw):
        return self.stroke([(x0, y), (x1, y)], **kw)

    def rring(self, x0, y0, x1, y1, r, **kw):
        return self.stroke([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], r, True, **kw)


def merge(parts):

    l = fontforge.layer()
    for p in parts:
        for c in p:
            l += c
    l.removeOverlap()
    l.correctDirection()
    return l
