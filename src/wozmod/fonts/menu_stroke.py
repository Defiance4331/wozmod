"""Menu stroke rendering support."""
import math
from bisect import bisect_right
from itertools import pairwise

import fontforge
import numpy as np

from .skeleton import Skeleton

OFFSET_TOLERANCE = 0.00005
_NODES, _WEIGHTS = np.polynomial.legendre.leggauss(16)
_CHECKS = np.arange(1, 16) / 16
_DIAGNOSTICS = []


def diagnostics() -> list[dict]:

    return [dict(row) for row in _DIAGNOSTICS]


def _point(skeleton, point):
    return np.array((point.x / skeleton.u,
                     skeleton.base - point.y / skeleton.u), dtype=float)


def _segments(skeleton, contour):
    points = list(contour)
    first = next(i for i, p in enumerate(points) if p.on_curve)
    points = points[first:] + points[:first]
    if contour.closed:
        points += points[:1]
    out, controls = [], []
    start = _point(skeleton, points[0])
    for p in points[1:]:
        q = _point(skeleton, p)
        if p.on_curve:
            pts = [start, *controls, q]
            if len(pts) not in (2, 4):
                raise ValueError("pinched stroke requires line or cubic centreline segments")
            if max(np.linalg.norm(a - start) for a in pts) > 1e-12:
                out.append(np.array(pts))
            start, controls = q, []
        else:
            controls.append(q)
    if controls or not out:
        raise ValueError("pinched stroke requires a nonempty complete centreline")
    return out


def _value(segment, t):
    if len(segment) == 2:
        return segment[0] * (1 - t) + segment[1] * t
    return ((1 - t)**3 * segment[0] + 3 * (1 - t)**2 * t * segment[1]
            + 3 * (1 - t) * t**2 * segment[2] + t**3 * segment[3])


def _derivatives(segment, t):
    if len(segment) == 2:
        return segment[1] - segment[0], np.zeros(2)
    velocity = (3 * (1 - t)**2 * (segment[1] - segment[0])
                + 6 * (1 - t) * t * (segment[2] - segment[1])
                + 3 * t**2 * (segment[3] - segment[2]))
    acceleration = (6 * (1 - t) * (segment[2] - 2*segment[1] + segment[0])
                    + 6 * t * (segment[3] - 2*segment[2] + segment[1]))
    return velocity, acceleration


def _arc(segment, t):
    if len(segment) == 2:
        return float(np.linalg.norm(segment[1] - segment[0])) * t
    ts = (_NODES + 1) * (t / 2)
    velocities = (3*(1-ts[:,None])**2*(segment[1]-segment[0])
                  + 6*(1-ts[:,None])*ts[:,None]*(segment[2]-segment[1])
                  + 3*ts[:,None]**2*(segment[3]-segment[2]))
    return float(np.sum(_WEIGHTS * np.linalg.norm(velocities, axis=1))) * t / 2


def _nearest(segment, point):
    if len(segment) == 2:
        v = segment[1] - segment[0]
        t = float(np.clip(np.dot(point - segment[0], v) / np.dot(v, v), 0, 1))
        return t, float(np.linalg.norm(_value(segment, t) - point))


    coefficient = np.array([segment[0] - point,
                            3*(segment[1] - segment[0]),
                            3*(segment[2] - 2*segment[1] + segment[0]),
                            segment[3] - 3*segment[2] + 3*segment[1] - segment[0]])
    polynomial = np.zeros(6)
    for axis in (0, 1):
        c = coefficient[:, axis]
        product=np.polynomial.polynomial.polymul(c, [c[1], 2*c[2], 3*c[3]])
        polynomial[:len(product)] += product
    candidates = [0.0, 1.0]
    candidates += [float(root.real) for root in np.polynomial.polynomial.polyroots(polynomial)
                   if abs(root.imag) < 1e-8 and 0 < root.real < 1]
    t = min(candidates, key=lambda q: np.linalg.norm(_value(segment, q) - point))
    return t, float(np.linalg.norm(_value(segment, t) - point))


def _falloff(u):
    return min(1.0, max(0.0, 1 - 10*u**3 + 15*u**4 - 6*u**5))


def _pressure_stations(centers, length, closed_length, boundaries):


    windows = []
    epsilon = 1e-10 * max(1.0, length)
    for center in centers:
        stations = [-length, 0.0, length]
        for boundary in boundaries:
            distance = float(boundary - center)
            if closed_length:
                distance = ((distance + closed_length / 2) % closed_length
                            - closed_length / 2)
            if not -length + epsilon < distance < length - epsilon:
                continue
            if any(abs(distance - station) <= epsilon for station in stations):
                continue
            stations.append(distance)
        stations.sort()
        windows.append((stations, [_falloff(abs(s) / length) for s in stations]))
    return windows


def _station_profile(distance, stations, values):

    i = min(len(stations) - 2, max(0, bisect_right(stations, distance) - 1))
    span = stations[i + 1] - stations[i]
    t = min(1.0, max(0.0, (distance - stations[i]) / span))
    change = values[i + 1] - values[i]
    smoothstep = t**3 * (10 - 15*t + 6*t*t)
    value = values[i] + change * smoothstep
    slope = change * 30*t*t*(1 - t)**2 / span
    return min(1.0, max(0.0, value)), slope


def _profile(s, centers, length, closed_length, windows=None):
    values, slopes = [], []
    for index, center in enumerate(centers):
        distance = s - center
        if closed_length:
            distance = (distance + closed_length / 2) % closed_length - closed_length / 2
        u = abs(distance) / length
        if u >= 1:
            continue
        if windows is None or len(windows[index][0]) == 3:


            value = _falloff(u)
            slope = (-30*u**2 + 60*u**3 - 30*u**4) * math.copysign(1, distance) / length
        else:
            value, slope = _station_profile(distance, *windows[index])
        values.append(value); slopes.append(slope)
    if not values:
        return 0.0, 0.0


    value = 1 - math.prod(1-v for v in values)
    slope = sum(slopes[i] * math.prod(1-values[j] for j in range(len(values)) if j != i)
                for i in range(len(values)))
    return value, slope


def _hermite(p0, d0, p1, d1, dt):
    return np.array([p0, p0 + d0*dt/3, p1 - d1*dt/3, p1])


def _approximate(fn, t0, t1, stats, level=0):
    p0, d0 = fn(t0); p1, d1 = fn(t1)
    curve = _hermite(p0, d0, p1, d1, t1-t0)
    error = max(float(np.linalg.norm(_value(curve, u) - fn(t0 + (t1-t0)*u)[0]))
                for u in _CHECKS)
    if error > OFFSET_TOLERANCE:
        if level >= 18:
            raise ValueError("pinched stroke failed its analytic offset tolerance")
        middle = (t0+t1)/2
        return (_approximate(fn,t0,middle,stats,level+1)
                + _approximate(fn,middle,t1,stats,level+1))
    stats['max_checked_offset_error'] = max(stats['max_checked_offset_error'], error)
    stats['offset_cubics'] += 1
    return [curve]


def _miter(a, da, b, db):
    cross = da[0]*db[1] - da[1]*db[0]
    if abs(cross) < 1e-10:
        return None
    v = b-a
    t = (v[0]*db[1] - v[1]*db[0]) / cross
    return a+t*da


def _fontpoint(skeleton, p):
    return p[0]*skeleton.u, (skeleton.base-p[1])*skeleton.u


def _edge_commands(edges, closed, max_miter):

    commands = [('M', edges[0][0][0])]
    for i, curves in enumerate(edges):
        if i:
            a, b = edges[i-1][-1], curves[0]
            if np.linalg.norm(a[-1]-b[0]) > 1e-9:
                m = _miter(a[-1], a[-1]-a[-2], b[0], b[1]-b[0])
                if m is not None and np.linalg.norm(m-a[-1]) <= max_miter:
                    commands.append(('L',m))
                commands.append(('L',b[0]))
        for c in curves:
            commands.append(('C',c[1],c[2],c[3]))
    if closed:
        a,b=edges[-1][-1],edges[0][0]
        if np.linalg.norm(a[-1]-b[0]) > 1e-9:
            m=_miter(a[-1],a[-1]-a[-2],b[0],b[1]-b[0])
            if m is not None and np.linalg.norm(m-a[-1]) <= max_miter:
                commands.append(('L',m))
            commands.append(('L',b[0]))
    return commands


def _contour(skeleton, commands):
    c=fontforge.contour()
    for command in commands:
        if command[0]=='M':c.moveTo(*_fontpoint(skeleton,command[1]))
        elif command[0]=='L':c.lineTo(*_fontpoint(skeleton,command[1]))
        else:c.cubicTo(*(_fontpoint(skeleton,p) for p in command[1:]))
    c.closed=True
    return c


def pinched_stroke(skeleton, pts, radii, closed, pen, joins, depth, length, box=None):


    if depth == 0 or not joins:
        return Skeleton.stroke(skeleton,pts,radii,closed,pen,box)
    if (not math.isfinite(depth) or depth < 0 or not math.isfinite(length) or length <= 0):
        raise ValueError("pinch depth must be nonnegative and recovery length positive")
    if len(pen) not in (2,3) or not all(math.isfinite(v) for v in pen) or min(pen[:2]) <= 0:
        raise ValueError("pinched stroke needs a finite positive elliptical pen")
    if depth >= min(pen[:2]):
        raise ValueError("pinch must leave positive stroke thickness at every normal")
    centerline=skeleton.contour(pts,radii,closed)
    segments=_segments(skeleton,centerline)
    lengths=[_arc(segment,1) for segment in segments]
    starts=np.r_[0,np.cumsum(lengths)[:-1]]
    total=float(sum(lengths))
    if closed and length >= total/2:
        raise ValueError("pinch recovery length must be less than half a closed stroke")
    area=sum(pts[i][0]*pts[(i+1)%len(pts)][1]-pts[(i+1)%len(pts)][0]*pts[i][1]
             for i in range(len(pts)))
    orientation=1 if area >= 0 else -1
    centers={1:[],-1:[]}
    for join in joins:
        side=1
        if isinstance(join,dict):
            point=join['point']
            side=join.get('side','outer')
            if side in ('outer',1):side=1
            elif side in ('inner',-1):side=-1
            else:raise ValueError("pinch join side must be outer or inner")
        else:point=join
        x,y=skeleton.fp(*point)
        target=np.array((x/skeleton.u,skeleton.base-y/skeleton.u))
        nearest=[_nearest(segment,target) for segment in segments]
        i=min(range(len(nearest)),key=lambda j:nearest[j][1])
        centers[side].append(float(starts[i]+_arc(segments[i],nearest[i][0])))
    boundaries = [*starts, total]
    windows = {side: _pressure_stations(side_centers, length, total if closed else 0,
                                       boundaries)
               for side, side_centers in centers.items()}
    angle=-(pen[2] if len(pen)==3 else 0)
    rotation=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
    ellipse=rotation@np.diag([(pen[0]/2)**2,(pen[1]/2)**2])@rotation.T
    stats=dict(depth=depth,length=length,joins=len(joins),offset_cubics=0,
               tolerance=OFFSET_TOLERANCE,max_checked_offset_error=0.0,
               checks_per_interval=len(_CHECKS),centerline_length=total)
    stats['pressure_station_count'] = sum(len(stations) for ws in windows.values()
                                         for stations, _ in ws)
    stats['internal_pressure_station_count'] = sum(len(stations) - 3
                                                  for ws in windows.values()
                                                  for stations, _ in ws)
    edges={1:[],-1:[]}
    for i,segment in enumerate(segments):


        breaks={0.0,1.0}
        for side, side_centers in centers.items():
            for center, (stations, _) in zip(side_centers, windows[side], strict=True):
                for shift in (-total,0,total) if closed else (0,):
                    for station in stations:
                        s = center + shift + station
                        if starts[i] < s < starts[i]+lengths[i]:
                            lo,hi=0.0,1.0
                            for _ in range(45):
                                mid=(lo+hi)/2
                                if _arc(segment,mid) < s-starts[i]:lo=mid
                                else:hi=mid
                            breaks.add((lo+hi)/2)
        for side in (1,-1):
            def edge(t,side=side):
                p=_value(segment,t);v,a=_derivatives(segment,t)
                speed=float(np.linalg.norm(v))
                if speed <= 1e-12:raise ValueError("pinched stroke centreline has a stationary tangent")
                tangent=v/speed
                dtangent=(a-tangent*np.dot(tangent,a))/speed
                n=orientation*np.array((tangent[1],-tangent[0]))*side
                dn=orientation*np.array((dtangent[1],-dtangent[0]))*side
                en=ellipse@n;h=math.sqrt(float(np.dot(n,en)))
                support=en/h
                dsupport=(ellipse@dn)/h-en*float(np.dot(dn,en))/h**3
                s=float(starts[i]+_arc(segment,t))
                profile,slope=_profile(s,centers[side],length,total if closed else 0,
                                       windows[side])
                return (p+support-n*depth*profile,
                        v+dsupport-dn*depth*profile-n*depth*slope*speed)
            curves=[]
            bs=sorted(breaks)
            for lo,hi in pairwise(bs):curves.extend(_approximate(edge,lo,hi,stats))
            edges[side].append(curves)
    layer=fontforge.layer()
    if closed:
        for side in (1,-1):layer+=_contour(skeleton,_edge_commands(edges[side],True,max(pen[:2])*8))
    else:
        outside=_edge_commands(edges[1],False,max(pen[:2])*8)
        inside=[list(reversed([c[::-1] for c in curves])) for curves in reversed(edges[-1])]
        back=_edge_commands(inside,False,max(pen[:2])*8)
        commands=outside+[('L',back[0][1])]+back[1:]
        layer+=_contour(skeleton,commands)
    layer.correctDirection()
    layer.removeOverlap()
    layer.correctDirection()
    if box:layer=skeleton.clip(layer,*box)
    _DIAGNOSTICS.append(stats)
    return layer
