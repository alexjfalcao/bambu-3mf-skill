#!/usr/bin/env python3
"""Watertight primitives for 3MF parts, tessellated from a chord tolerance.

Every function returns (vertices, triangles) with vertices as [x, y, z] in mm
and triangles as [i, j, k] indices, wound counter-clockwise seen from outside.
Closed and manifold: each edge is shared by exactly two triangles, which is what
the slicer needs and what validate_3mf.py / trimesh will check.

    from solids import cylinder, ring, extrude, revolve, segments_for

Curved surfaces take `tol`, the chord deviation in mm — feed it the tolerance of
the quality level you are exporting at (test 0.30, good 0.10, extrafine 0.03).
"""

import math


def segments_for(radius, tol=0.1, minimum=12, maximum=2048):
    """Segment count so the chord never deviates more than `tol` from the arc."""
    if radius <= 0 or tol <= 0:
        return minimum
    if tol >= radius:
        return minimum
    n = math.ceil(math.pi / math.acos(1.0 - tol / radius))
    return max(minimum, min(maximum, int(n)))


def _weld(vertices, triangles):
    """Merge vertices that round to the same 1e-5 mm, dropping degenerates.
    Two rings computed separately must land on the SAME index or the mesh opens
    up along the seam."""
    index, out_v, remap = {}, [], []
    for v in vertices:
        key = (round(v[0], 5), round(v[1], 5), round(v[2], 5))
        if key not in index:
            index[key] = len(out_v)
            out_v.append([key[0], key[1], key[2]])
        remap.append(index[key])
    out_t = []
    for a, b, c in triangles:
        a, b, c = remap[a], remap[b], remap[c]
        if a != b and b != c and a != c:
            out_t.append([a, b, c])
    return out_v, out_t


def box(width, depth, height, center=(0.0, 0.0), z0=0.0):
    """Axis-aligned box sitting on z0, centered on `center` in XY."""
    cx, cy = center
    x0, x1 = cx - width / 2.0, cx + width / 2.0
    y0, y1 = cy - depth / 2.0, cy + depth / 2.0
    z1 = z0 + height
    v = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
         [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    t = [[0, 2, 1], [0, 3, 2],          # bottom (pointing down)
         [4, 5, 6], [4, 6, 7],          # top
         [0, 1, 5], [0, 5, 4],
         [1, 2, 6], [1, 6, 5],
         [2, 3, 7], [2, 7, 6],
         [3, 0, 4], [3, 4, 7]]
    return v, t


def cylinder(radius, height, tol=0.1, center=(0.0, 0.0), z0=0.0, segments=None):
    """Solid cylinder standing on z0. Center vertices are shared, not duplicated."""
    n = segments or segments_for(radius, tol)
    cx, cy = center
    z1 = z0 + height
    v = [[cx, cy, z0], [cx, cy, z1]]
    for i in range(n):
        a = 2 * math.pi * i / n
        v.append([cx + radius * math.cos(a), cy + radius * math.sin(a), z0])
        v.append([cx + radius * math.cos(a), cy + radius * math.sin(a), z1])
    t = []
    for i in range(n):
        b0, b1 = 2 + 2 * i, 2 + 2 * ((i + 1) % n)
        t0, t1 = b0 + 1, b1 + 1
        t.append([0, b1, b0])           # bottom fan, pointing down
        t.append([1, t0, t1])           # top fan
        t.append([b0, b1, t1])          # wall
        t.append([b0, t1, t0])
    return _weld(v, t)


def ring(inner_radius, outer_radius, height, tol=0.1, center=(0.0, 0.0), z0=0.0,
         segments=None):
    """Annulus (tube) — a cylinder with a through hole."""
    if inner_radius <= 0:
        return cylinder(outer_radius, height, tol, center, z0, segments)
    n = segments or segments_for(outer_radius, tol)
    cx, cy = center
    z1 = z0 + height
    v = []
    for i in range(n):
        a = 2 * math.pi * i / n
        ca, sa = math.cos(a), math.sin(a)
        v += [[cx + inner_radius * ca, cy + inner_radius * sa, z0],
              [cx + outer_radius * ca, cy + outer_radius * sa, z0],
              [cx + inner_radius * ca, cy + inner_radius * sa, z1],
              [cx + outer_radius * ca, cy + outer_radius * sa, z1]]
    t = []
    for i in range(n):
        a0 = 4 * i
        a1 = 4 * ((i + 1) % n)
        i0, o0, i0t, o0t = a0, a0 + 1, a0 + 2, a0 + 3
        i1, o1, i1t, o1t = a1, a1 + 1, a1 + 2, a1 + 3
        t += [[i0, o1, o0], [i0, i1, o1],           # bottom band (down)
              [i0t, o0t, o1t], [i0t, o1t, i1t],     # top band
              [o0, o1, o1t], [o0, o1t, o0t],        # outer wall
              [i0, i0t, i1t], [i0, i1t, i1]]        # inner wall (flipped)
    return _weld(v, t)


def _area(poly):
    s = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def triangulate(poly):
    """Ear clipping for a simple polygon without holes. Returns index triples
    into `poly`, wound counter-clockwise."""
    pts = list(poly)
    if _area(pts) < 0:
        pts = pts[::-1]
        flip = True
    else:
        flip = False
    idx = list(range(len(pts)))

    def cross(o, a, b):
        return ((pts[a][0] - pts[o][0]) * (pts[b][1] - pts[o][1])
                - (pts[a][1] - pts[o][1]) * (pts[b][0] - pts[o][0]))

    def inside(p, a, b, c):
        d1 = cross(a, b, p)
        d2 = cross(b, c, p)
        d3 = cross(c, a, p)
        return (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0)

    out, guard = [], 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross(a, b, c) <= 0:
                continue                                    # reflex corner
            if any(inside(p, a, b, c) for p in idx if p not in (a, b, c)):
                continue                                    # another point inside
            out.append((a, b, c))
            idx.pop(k)
            break
        else:
            break                                           # not a simple polygon
    if len(idx) == 3:
        out.append(tuple(idx))
    n = len(pts)
    if flip:
        # reverter a lista troca so os indices, nao espelha a geometria:
        # manter a ordem ciclica preserva o sentido anti-horario
        out = [(n - 1 - a, n - 1 - b, n - 1 - c) for a, b, c in out]
    return out


def extrude(polygon_xy, height, z0=0.0):
    """Straight extrusion of a simple closed polygon (no holes), [[x, y], ...]."""
    if len(polygon_xy) < 3:
        raise ValueError("polygon needs at least 3 points")
    caps = triangulate(polygon_xy)
    n = len(polygon_xy)
    z1 = z0 + height
    v = [[p[0], p[1], z0] for p in polygon_xy] + [[p[0], p[1], z1] for p in polygon_xy]
    t = []
    for a, b, c in caps:
        t.append([a, c, b])                                  # bottom, pointing down
        t.append([n + a, n + b, n + c])                       # top
    ccw = _area(polygon_xy) > 0
    for i in range(n):
        j = (i + 1) % n
        if ccw:
            t += [[i, j, n + j], [i, n + j, n + i]]
        else:
            t += [[i, n + j, j], [i, n + i, n + j]]
    return _weld(v, t)


def revolve(profile_rz, tol=0.1, center=(0.0, 0.0), segments=None, radius_hint=None):
    """Revolve a profile around the Z axis. `profile_rz` is [[r, z], ...] with
    r >= 0, ordered from bottom to top; the solid is closed by the axis, so the
    first and last points should sit on r = 0 (a cone, dome, vase wall...)."""
    prof = [(float(r), float(z)) for r, z in profile_rz]
    if len(prof) < 2:
        raise ValueError("profile needs at least 2 points")
    rmax = radius_hint or max(r for r, _ in prof)
    n = segments or segments_for(rmax, tol)
    cx, cy = center
    v, ring_index = [], []
    for r, z in prof:
        if r <= 1e-9:
            v.append([cx, cy, z])
            ring_index.append(("axis", len(v) - 1))
        else:
            base = len(v)
            for i in range(n):
                a = 2 * math.pi * i / n
                v.append([cx + r * math.cos(a), cy + r * math.sin(a), z])
            ring_index.append(("ring", base))
    t = []
    for k in range(len(prof) - 1):
        kind0, b0 = ring_index[k]
        kind1, b1 = ring_index[k + 1]
        for i in range(n):
            j = (i + 1) % n
            if kind0 == "axis" and kind1 == "ring":
                t.append([b0, b1 + j, b1 + i])
            elif kind0 == "ring" and kind1 == "axis":
                t.append([b0 + i, b0 + j, b1])
            elif kind0 == "ring" and kind1 == "ring":
                t += [[b0 + i, b0 + j, b1 + j], [b0 + i, b1 + j, b1 + i]]
    # cap open ends so the solid closes even if the profile does not touch the axis
    for k, side in ((0, "bottom"), (len(prof) - 1, "top")):
        kind, b = ring_index[k]
        if kind != "ring":
            continue
        r, z = prof[k]
        v.append([cx, cy, z])
        c = len(v) - 1
        for i in range(n):
            j = (i + 1) % n
            t.append([c, b + i, b + j] if side == "bottom" else [c, b + j, b + i])
    return _weld(v, t)


def audit(vertices, triangles):
    """{'open_edges', 'degenerate', 'triangles'} — open_edges must be 0."""
    edges, degen = {}, 0
    for a, b, c in triangles:
        if a == b or b == c or a == c:
            degen += 1
        for e in ((a, b), (b, c), (c, a)):
            rev = (e[1], e[0])
            if edges.get(rev):
                edges[rev] -= 1
            else:
                edges[e] = edges.get(e, 0) + 1
    return {"open_edges": sum(edges.values()), "degenerate": degen,
            "triangles": len(triangles), "vertices": len(vertices)}


if __name__ == "__main__":
    for name, (v, t) in [
        ("box", box(20, 20, 5)),
        ("cylinder", cylinder(30, 4, 0.03)),
        ("ring", ring(20, 30, 4, 0.03)),
        ("extrude", extrude([[0, 0], [40, 0], [40, 10], [18, 10], [18, 30], [0, 30]], 3)),
        ("revolve", revolve([[0, 0], [25, 0], [25, 2], [10, 12], [0, 14]], 0.03)),
    ]:
        print("%-9s %s" % (name, audit(v, t)))
