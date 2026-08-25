#!/usr/bin/env python3
"""Render img/coaster-transparent.png — the same coaster on a transparent
background, for pages that want it on their own colour.

Reuses the geometry, camera and shading from render_cover.py; the differences
are RGBA output and alpha taken from pixel coverage.

    python3 img/render_transparent.py [out.png]
"""

import array
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render_cover as rc  # noqa: E402

WIDTH, HEIGHT = 1200, 700
SS = 3
SCALE = 9.2                                    # mm -> px at final resolution


def render_rgba():
    parts = rc.build()
    eye, right, up = rc.camera()
    light = rc.normalise((-0.45, -0.30, 0.84))

    w, h = WIDTH * SS, HEIGHT * SS
    scale = SCALE * SS
    cx, cy = w * 0.5, h * 0.50

    colour = bytearray(3 * w * h)
    covered = bytearray(w * h)                 # 1 where a triangle landed
    depth = array.array("f", [-1e30]) * (w * h)

    for verts, tris, hexcol in parts:
        base_rgb = rc.hex_rgb(hexcol)
        proj = [(cx + rc.dot(p, right) * scale,
                 cy - rc.dot(p, up) * scale,
                 rc.dot(p, eye)) for p in verts]

        for a, b, c in tris:
            va, vb, vc = verts[a], verts[b], verts[c]
            n = rc.normalise(rc.cross([vb[i] - va[i] for i in range(3)],
                                      [vc[i] - va[i] for i in range(3)]))
            if rc.dot(n, eye) <= 0.0:
                continue
            lam = max(0.0, rc.dot(n, light))
            shade = 0.30 + 0.70 * lam ** 0.8
            rgb = tuple(min(255, int(ch * shade + 34 * lam ** 8)) for ch in base_rgb)
            fill(colour, covered, depth, w, h, proj[a], proj[b], proj[c], rgb)

    return downsample(colour, covered, w, h)


def fill(colour, covered, depth, w, h, pa, pb, pc, rgb):
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
            if z <= depth[j]:
                continue
            depth[j] = z
            covered[j] = 1
            i = j * 3
            colour[i] = r
            colour[i + 1] = g
            colour[i + 2] = b


def downsample(colour, covered, w, h):
    """Average colour over the *covered* subsamples only — averaging in the
    transparent ones would darken every silhouette edge."""
    out = bytearray(WIDTH * HEIGHT * 4)
    n = SS * SS
    for y in range(HEIGHT):
        for x in range(WIDTH):
            r = g = b = hits = 0
            for dy in range(SS):
                base = (y * SS + dy) * w + x * SS
                for dx in range(SS):
                    j = base + dx
                    if not covered[j]:
                        continue
                    hits += 1
                    i = j * 3
                    r += colour[i]
                    g += colour[i + 1]
                    b += colour[i + 2]
            o = (y * WIDTH + x) * 4
            if hits:
                out[o] = r // hits
                out[o + 1] = g // hits
                out[o + 2] = b // hits
                out[o + 3] = (hits * 255) // n
    return out


def write_png(path, pixels):
    raw = bytearray()
    for y in range(HEIGHT):
        raw.append(0)
        raw += pixels[y * WIDTH * 4:(y + 1) * WIDTH * 4]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        HERE, "coaster-transparent.png")
    write_png(out, render_rgba())
    print(f"{out}  {WIDTH}x{HEIGHT}  {os.path.getsize(out)} bytes")
