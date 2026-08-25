#!/usr/bin/env python3
"""Render img/og.png — the repo cover.

Builds a three-colour coaster with the skill's own primitives, then rasterises it
with a small z-buffered software renderer. Standard library only, so the cover
can be regenerated anywhere:

    python3 img/render_cover.py

Each colour is a separate closed solid, exactly as the skill requires — the image
is the geometry, not a mock-up of it.
"""

import array
import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "skills", "bambu-3mf", "scripts"))

import solids  # noqa: E402

WIDTH, HEIGHT = 1280, 640
SS = 3                                   # supersampling factor
BG = (0x12, 0x0E, 0x1A)

PURPLE = "#5B2A7A"
GOLD = "#F2D02C"
RED = "#C8452F"

TOL = 0.05


# ----------------------------------------------------------------- geometry

def petal(angle, r_in, r_out, half_width_deg, steps=14):
    """A lens-shaped petal between two radii, as a closed XY polygon."""
    hw = math.radians(half_width_deg)
    pts = []
    for i in range(steps + 1):                       # outward along one edge
        t = i / steps
        r = r_in + (r_out - r_in) * t
        a = angle + hw * math.sin(math.pi * t)
        pts.append([r * math.cos(a), r * math.sin(a)])
    for i in range(steps - 1, 0, -1):                # back along the other,
        t = i / steps                                # tips are single points
        r = r_in + (r_out - r_in) * t
        a = angle - hw * math.sin(math.pi * t)
        pts.append([r * math.cos(a), r * math.sin(a)])
    return pts


def build():
    """[(vertices, triangles, '#rrggbb')] — one closed solid per colour."""
    base_h = 2.4
    base = solids.cylinder(46, base_h, TOL)
    parts = [(base[0], base[1], PURPLE)]

    rim = solids.ring(42, 46, 3.0, TOL, z0=base_h)
    inner = solids.ring(17, 19, 2.2, TOL, z0=base_h)
    gold_v, gold_t = rim[0], list(rim[1])
    off = len(gold_v)
    gold_v = gold_v + inner[0]
    gold_t += [[a + off, b + off, c + off] for a, b, c in inner[1]]
    parts.append((gold_v, gold_t, GOLD))

    red_v, red_t = [], []
    for k in range(12):
        a = 2 * math.pi * k / 12
        v, t = solids.extrude(petal(a, 21, 40, 11.0), 2.2)
        off = len(red_v)
        red_v += [[x, y, z + base_h] for x, y, z in v]
        red_t += [[i + off, j + off, m + off] for i, j, m in t]
    parts.append((red_v, red_t, RED))

    for v, t, colour in parts:                       # the skill's own invariant
        report = solids.audit(v, t)
        assert report["open_edges"] == 0, (colour, report)
    return parts


# ------------------------------------------------------------------ raster

def hex_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def normalise(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def camera(elevation_deg=34.0, azimuth_deg=38.0):
    """Look-at basis. `eye` is the unit direction from the model to the camera;
    `right`/`up` span the screen plane. Depth is dot(p, eye) — bigger is nearer."""
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    eye = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))
    right = normalise(cross((0.0, 0.0, 1.0), eye))
    up = cross(eye, right)
    return eye, right, up


def render():
    parts = build()
    eye, right, up = camera()
    light = normalise((-0.45, -0.30, 0.84))

    faces = []                                       # (rgb, three screen points)
    w, h = WIDTH * SS, HEIGHT * SS
    scale = 8.6 * SS
    cx, cy = w * 0.5, h * 0.50

    for verts, tris, colour in parts:
        base_rgb = hex_rgb(colour)
        proj = []
        for p in verts:
            proj.append((cx + dot(p, right) * scale,   # screen y grows downward
                         cy - dot(p, up) * scale,
                         dot(p, eye)))

        for a, b, c in tris:
            va, vb, vc = verts[a], verts[b], verts[c]
            n = normalise(cross([vb[i] - va[i] for i in range(3)],
                                [vc[i] - va[i] for i in range(3)]))
            if dot(n, eye) <= 0.0:
                continue                             # facing away from camera
            lam = max(0.0, dot(n, light))
            shade = 0.30 + 0.70 * lam ** 0.8
            rgb = tuple(min(255, int(ch * shade + 34 * lam ** 8)) for ch in base_rgb)
            faces.append((rgb, proj[a], proj[b], proj[c]))

    buf = bytearray(BG * (w * h))
    depth = array.array("f", [-1e30]) * (w * h)      # nothing drawn yet
    for rgb, pa, pb, pc in faces:
        fill(buf, depth, w, h, pa, pb, pc, rgb)
    return downsample(buf, w, h)


def fill(buf, depth, w, h, pa, pb, pc, rgb):
    """Scanline fill with per-pixel depth test — a painter's sort by centroid
    tears where a big triangle meets a small one, which is exactly what a flat
    base plus small inlays looks like."""
    ax, ay, az = pa
    bx, by, bz = pb
    cx_, cy_, cz = pc
    ymin = max(0, int(min(ay, by, cy_)))
    ymax = min(h - 1, int(max(ay, by, cy_)) + 1)
    xmin = max(0, int(min(ax, bx, cx_)))
    xmax = min(w - 1, int(max(ax, bx, cx_)) + 1)
    if ymin > ymax or xmin > xmax:
        return
    d = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
    if abs(d) < 1e-9:
        return
    r, g, b = rgb
    for py in range(ymin, ymax + 1):
        yc = py + 0.5 - cy_
        row = py * w
        for px in range(xmin, xmax + 1):
            xc = px + 0.5 - cx_
            l1 = ((by - cy_) * xc + (cx_ - bx) * yc) / d
            if l1 < 0.0 or l1 > 1.0:
                continue
            l2 = ((cy_ - ay) * xc + (ax - cx_) * yc) / d
            if l2 < 0.0 or l1 + l2 > 1.0:
                continue
            z = l1 * az + l2 * bz + (1.0 - l1 - l2) * cz
            j = row + px
            if z <= depth[j]:                        # something nearer is there
                continue
            depth[j] = z
            i = j * 3
            buf[i] = r
            buf[i + 1] = g
            buf[i + 2] = b


def downsample(buf, w, h):
    out = bytearray(WIDTH * HEIGHT * 3)
    n = SS * SS
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r = g = b = 0
            for dy in range(SS):
                base = ((y * SS + dy) * w + x * SS) * 3
                for dx in range(SS):
                    i = base + dx * 3
                    r += buf[i]
                    g += buf[i + 1]
                    b += buf[i + 2]
            o = (y * WIDTH + x) * 3
            out[o] = r // n
            out[o + 1] = g // n
            out[o + 2] = b // n
    return out


def write_png(path, pixels):
    raw = bytearray()
    for y in range(HEIGHT):
        raw.append(0)                                # filter: none
        raw += pixels[y * WIDTH * 3:(y + 1) * WIDTH * 3]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


if __name__ == "__main__":
    out = os.path.join(HERE, "og.png")
    write_png(out, render())
    print(f"{out}  {WIDTH}x{HEIGHT}  {os.path.getsize(out)} bytes")
