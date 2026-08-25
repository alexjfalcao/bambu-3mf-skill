#!/usr/bin/env python3
"""Write a Bambu-Studio-compatible 3MF: one closed solid per color, each bound
to its own filament/extruder, with the color table written INTO the file.

Standard library only (zipfile + json). Import it, or run it on a JSON job file:

    python3 write_3mf.py job.json out.3mf --quality good --printer h2c

Job file format (coordinates in millimeters, Z up, model sitting on z=0):

    {"parts": [
       {"name": "body",  "color": "#5B2A7A",
        "vertices": [[x,y,z], ...], "triangles": [[i,j,k], ...]},
       {"name": "inlay", "color": "#F6F2E8", "vertices": [...], "triangles": [...]}
    ]}

Each part becomes: <object> in 3dmodel.model  ->  <part> with extruder i+1 in
model_settings.config  ->  filament i+1 with that color in project_settings.config.

Why all three: Bambu Studio ignores <basematerials>. model_settings.config alone
sends the part to the right extruder, but the color shown is whatever filament
the user has in that slot. See reference/format.md.

The project config is a complete machine profile (h2c / a1 / p1s / x1c) with its
per-filament tables resized for the palette. Those sizes are the part that bites:
they never fail to load, only to slice. See project_config() below.
"""

import argparse
import json
import os
import sys
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(AQUI, "..", "assets")
TEMPLATE = os.path.join(ASSETS, "project_template.json")     # H2C, presets resolved
DELTAS = os.path.join(ASSETS, "machine_deltas.json")         # a1 / p1s / x1c
REPEAT_KEYS = os.path.join(ASSETS, "per_filament_keys.json")  # the 145 per-filament keys

# The template is the H2C; the others are the ~200 keys that differ. They are
# single-extruder machines, so their "per extruder" and "per variant" arrays
# change LENGTH — swapping the whole key is what keeps that free of logic.
PRINTERS = ("h2c", "a1", "p1s", "x1c")

# The Application string is the trigger: with anything else, Bambu parses
# project_settings.config and throws it away. The version must be numeric.
BBS_VERSION = "02.08.02.61"

# Mesh tessellation budget + matching layer height. `tolerance` is the chord
# deviation to use when YOU tessellate curves/implicit surfaces; the writer only
# stamps the layer height into the project and reports the triangle count.
QUALITY = {
    "test":      {"tolerance": 0.30, "layer_height": 0.24, "max_tris": 200_000},
    "good":      {"tolerance": 0.10, "layer_height": 0.20, "max_tris": 1_500_000},
    "extrafine": {"tolerance": 0.03, "layer_height": 0.08, "max_tris": 6_000_000},
}

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    '<Default Extension="config" ContentType="text/xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rel0" Target="/3D/3dmodel.model" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
)


def hex6(color):
    """'#5b2a7a' / '5B2A7A' / '#abc' -> '#5B2A7A'. Exact, never approximated."""
    h = str(color).replace("#", "").upper()
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    if len(h) < 6 or any(c not in "0123456789ABCDEF" for c in h[:6]):
        raise ValueError("bad color: %r" % (color,))
    return "#" + h[:6]


def num(v):
    """5 decimals, not 3: on fine meshes neighbouring vertices can sit 0.0005 mm
    apart, and rounding them together makes degenerate triangles in the file."""
    return repr(round(float(v), 5) + 0.0).rstrip("0").rstrip(".") or "0"


def profile(printer="h2c", template=TEMPLATE):
    """Full machine profile: the H2C template with that machine's delta on top."""
    if printer not in PRINTERS:
        printer = "h2c"
    with open(template, encoding="utf8") as f:
        cfg = json.load(f)
    if printer != "h2c":
        with open(DELTAS, encoding="utf8") as f:
            cfg.update(json.load(f)[printer])
    return cfg


def project_config(colors, quality="good", template=TEMPLATE, extras=None,
                   printer="h2c", bbox=None):
    """Complete project_settings.config with one filament per color.

    A config carrying only filament_colour is parsed and then IGNORED by Bambu
    when it rebuilds the preset bundle (measured). It has to be a full profile,
    hence the template.

    Replicating that profile for N colors is NOT "repeat every length-1 array".
    The template describes ONE filament with V extruder variants, and several
    tables are sized by something other than N. Getting any of them wrong
    produces no read error at all — the file opens and fails at slicing:

      filament_self_index all "1"  -> "could not found extruder_type ...,
                                       filament_index 2..N" then
                                      "No valid nozzle found."
      empty extruder_nozzle_stats  -> "No valid nozzle found."
      wrong flush matrix size      -> "Flush volumes matrix do not match to
                                       the correct size!" (CLI and GUI)
      tower outside the common box -> "Found G-code in unprintable area of
                                       multi-extruder printers"
    """
    cfg = profile(printer, template)
    n = len(colors)
    # V and B come from the TEMPLATE, before anything is replicated.
    variants = cfg.get("filament_extruder_variant") or ["x"]
    v = len(variants)
    b = len(cfg.get("nozzle_diameter") or ["0.4"])

    # Per-filament keys: the template's block (1 or V entries) repeated per color.
    # The list was built empirically — comparing a 1-filament dump against a real
    # 8-filament project, every key whose array grows exactly x8. It cannot be
    # guessed from the name: nozzle_temperature is per filament and wipe_tower_x
    # is not, and neither starts with "filament".
    with open(REPEAT_KEYS, encoding="utf8") as f:
        for k in json.load(f):
            block = cfg.get(k)
            if isinstance(block, list) and block:
                cfg[k] = block * n

    cfg["filament_colour"] = [hex6(c) for c in colors]
    # which filament each variant entry belongs to: V entries "1", V entries "2"...
    cfg["filament_self_index"] = [str(i + 1) for i in range(n) for _ in range(v)]
    # one N×N block PER NOZZLE, stacked: b*N*N entries, each block zero-diagonal
    cfg["flush_volumes_matrix"] = [("0" if i == j else "280")
                                   for _ in range(b) for i in range(n) for j in range(n)]
    cfg["flush_volumes_vector"] = ["140"] * (2 * n)
    cfg["flush_multiplier"] = ["1"] * b
    cfg["flush_multiplier_fast"] = ["1.2"] * b
    types = cfg.get("nozzle_volume_type") or []
    slots = cfg.get("extruder_max_nozzle_count") or []
    stats = ["%s#%s" % (t, slots[i] if i < len(slots) else "1")
             for i, t in enumerate(types)]
    if stats:
        cfg["extruder_nozzle_stats"] = stats
    # process + N filaments + machine
    cfg["inherits_group"] = [""] * (n + 2)
    cfg["different_settings_to_system"] = [""] * (n + 2)

    tx, ty = wipe_tower(cfg, bbox)
    cfg["wipe_tower_x"] = [num(tx)]
    cfg["wipe_tower_y"] = [num(ty)]

    q = QUALITY[quality]
    cfg["layer_height"] = str(q["layer_height"])
    if float(cfg.get("initial_layer_print_height", 0.2)) < q["layer_height"]:
        cfg["initial_layer_print_height"] = str(q["layer_height"])
    cfg.update(extras or {})
    return json.dumps(cfg, indent=4)


def common_box(cfg):
    """Rectangle every extruder can reach — the intersection of
    extruder_printable_area, not the bed.

    On an H2C extruder 1 spans x=0..325 and extruder 2 x=25..330. Anything the
    tool has to place itself (the wipe tower) belongs in the intersection;
    outside it the slicer aborts with "Found G-code in unprintable area of
    multi-extruder printers"."""
    lo = [-1e9, -1e9]
    hi = [1e9, 1e9]
    found = False
    for area in cfg.get("extruder_printable_area", []):
        xs, ys = [], []
        for corner in str(area).split(","):
            q = corner.split("x")
            if len(q) == 2:
                xs.append(float(q[0]))
                ys.append(float(q[1]))
        if not xs:
            continue
        found = True
        lo = [max(lo[0], min(xs)), max(lo[1], min(ys))]
        hi = [min(hi[0], max(xs)), min(hi[1], max(ys))]
    if found:
        return lo, hi
    xs, ys = [], []
    for p in cfg.get("printable_area", []):
        q = str(p).split("x")
        if len(q) == 2:
            xs.append(float(q[0]))
            ys.append(float(q[1]))
    if not xs:
        return [0.0, 0.0], [0.0, 0.0]
    return [min(xs), min(ys)], [max(xs), max(ys)]


def wipe_tower(cfg, bbox=None):
    """Tower corner: behind the object, centered in x, clamped to the common box.

    The template ships the slicer's default 15/220, and x=15 is out of reach of
    the H2C's second extruder. `bbox` is the model's ((minx,miny),(maxx,maxy))
    in bed coordinates, i.e. after the build translation."""
    lo, hi = common_box(cfg)
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    w = float(cfg.get("prime_tower_width") or 60)
    x, y = cx - w / 2.0, cy
    if bbox:
        x = (bbox[0][0] + bbox[1][0]) / 2.0 - w / 2.0
        y = bbox[1][1] + 12.0
    x = min(max(x, lo[0] + 2), max(lo[0] + 2, hi[0] - w - 2))
    y = min(max(y, lo[1] + 2), max(lo[1] + 2, hi[1] - w - 2))
    return x, y


def bed_center(cfg):
    """Bed center from the profile's printable_area ("XxY" per corner).

    A project 3MF is placed where the <build><item> transform says, with no
    auto-arrange. Parts modeled around the origin therefore open centered on
    (0,0) — the front-left corner — mostly off the plate. Model wherever you
    like and let this put the object on the bed."""
    xs, ys = [], []
    for p in cfg.get("printable_area", []):
        q = str(p).split("x")
        if len(q) == 2:
            xs.append(float(q[0]))
            ys.append(float(q[1]))
    if not xs:
        return 0.0, 0.0
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def object_xml(vertices, triangles, obj_id, pindex, out):
    out.append('<object id="%d" type="model" pid="1" pindex="%d">\n<mesh>\n<vertices>\n'
               % (obj_id, pindex))
    for v in vertices:
        out.append('<vertex x="%s" y="%s" z="%s"/>\n' % (num(v[0]), num(v[1]), num(v[2])))
    out.append("</vertices>\n<triangles>\n")
    for t in triangles:
        out.append('<triangle v1="%d" v2="%d" v3="%d"/>\n' % (t[0], t[1], t[2]))
    out.append("</triangles>\n</mesh>\n</object>\n")


def write_3mf(parts, path, name="model", quality="good", template=TEMPLATE, extras=None,
              printer="h2c"):
    """parts: list of dicts with color, vertices, triangles and optional name.

    Part i -> object id 2+i -> <part> with extruder i+1 -> filament i+1, whose
    color is part i's color. Returns a small summary dict."""
    if quality not in QUALITY:
        raise ValueError("quality must be one of %s" % ", ".join(QUALITY))
    if not parts:
        raise ValueError("no parts")
    title = "".join(c for c in str(name) if c not in '<>&"\'')
    colors = [hex6(p["color"]) for p in parts]
    cx, cy = bed_center(profile(printer, template))
    # model bbox in BED coordinates — the wipe tower goes behind it
    pts = [v for p in parts for v in p["vertices"]]
    bbox = None
    if pts:
        bbox = ((min(v[0] for v in pts) + cx, min(v[1] for v in pts) + cy),
                (max(v[0] for v in pts) + cx, max(v[1] for v in pts) + cy))
    project = project_config(colors, quality, template, extras, printer, bbox)

    m = ['<?xml version="1.0" encoding="UTF-8"?>\n'
         '<model unit="millimeter" xml:lang="en-US" '
         'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
         # the trigger — see reference/format.md
         '<metadata name="Application">BambuStudio-%s</metadata>\n' % BBS_VERSION,
         '<metadata name="Title">%s</metadata>\n' % title,
         '<resources>\n<basematerials id="1">\n']
    # kept for generic 3MF viewers only; Bambu ignores basematerials
    for i, c in enumerate(colors):
        m.append('<base name="color%d" displaycolor="%sFF"/>\n' % (i, c))
    m.append("</basematerials>\n")

    total = 0
    for i, p in enumerate(parts):
        tris = p["triangles"]
        total += len(tris)
        object_xml(p["vertices"], tris, 2 + i, i, m)
    root = 2 + len(parts)
    m.append('<object id="%d" type="model">\n<components>\n' % root)
    for i in range(len(parts)):
        m.append('<component objectid="%d"/>\n' % (2 + i))
    m.append("</components>\n</object>\n")
    # no auto-arrange in a project: place the object on the bed explicitly
    m.append('</resources>\n<build><item objectid="%d" '
             'transform="1 0 0 0 1 0 0 0 1 %s %s 0" printable="1"/></build>\n</model>\n'
             % (root, num(cx), num(cy)))

    c = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="%d">\n' % root,
         '    <metadata key="name" value="%s"/>\n' % title,
         '    <metadata key="extruder" value="1"/>\n']
    for i, p in enumerate(parts):
        c.append('    <part id="%d" subtype="normal_part">\n'
                 '      <metadata key="name" value="%s"/>\n'
                 '      <metadata key="extruder" value="%d"/>\n'
                 '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                 "    </part>\n"
                 % (2 + i, "%s %s" % (p.get("name", title), colors[i]), i + 1))
    c.append("  </object>\n</config>\n")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", "".join(m))
        z.writestr("Metadata/model_settings.config", "".join(c))
        z.writestr("Metadata/project_settings.config", project)

    cfg = json.loads(project)
    budget = QUALITY[quality]["max_tris"]
    return {"path": path, "parts": len(parts), "triangles": total, "colors": colors,
            "quality": quality, "over_budget": total > budget, "printer": printer,
            "placed_at": (cx, cy), "bytes": os.path.getsize(path),
            "tower_at": (float(cfg["wipe_tower_x"][0]), float(cfg["wipe_tower_y"][0])),
            "fits_bed": bbox is not None and _fits(cfg, bbox)}


def _fits(cfg, bbox):
    lo, hi = common_box(cfg)
    return (bbox[0][0] >= lo[0] and bbox[0][1] >= lo[1]
            and bbox[1][0] <= hi[0] and bbox[1][1] <= hi[1])


def main():
    ap = argparse.ArgumentParser(description="Write a Bambu-compatible 3MF.")
    ap.add_argument("job", help="JSON file with {'parts': [...]}, or - for stdin")
    ap.add_argument("out", help="output .3mf")
    ap.add_argument("--quality", default="good", choices=sorted(QUALITY))
    ap.add_argument("--printer", default="h2c", choices=PRINTERS)
    ap.add_argument("--name", default=None, help="model name (default: output basename)")
    a = ap.parse_args()
    job = json.load(sys.stdin if a.job == "-" else open(a.job, encoding="utf8"))
    name = a.name or os.path.splitext(os.path.basename(a.out))[0]
    r = write_3mf(job["parts"], a.out, name=name, quality=a.quality,
                  extras=job.get("project_overrides"), printer=a.printer)
    print("%(path)s  %(parts)d parts  %(triangles)d triangles  %(bytes)d bytes" % r)
    print("  printer   %s, placed at %.1f, %.1f, tower at %.1f, %.1f"
          % (r["printer"], r["placed_at"][0], r["placed_at"][1],
             r["tower_at"][0], r["tower_at"][1]))
    for i, c in enumerate(r["colors"]):
        print("  filament %d -> %s" % (i + 1, c))
    if not r["fits_bed"]:
        print("WARNING: the model does not fit the area every extruder can reach",
              file=sys.stderr)
    if r["over_budget"]:
        print("WARNING: over the %s triangle budget (%d)"
              % (a.quality, QUALITY[a.quality]["max_tris"]), file=sys.stderr)


if __name__ == "__main__":
    main()
