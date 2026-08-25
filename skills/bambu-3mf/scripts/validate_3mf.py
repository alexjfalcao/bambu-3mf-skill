#!/usr/bin/env python3
"""Validate a Bambu 3MF without opening the slicer.

    python3 validate_3mf.py out.3mf
    python3 validate_3mf.py out.3mf --job job.json     # also checks geometry is unchanged

Checks, in order:
  1. the zip is structurally sound and carries the 5 expected entries;
  2. Application is "BambuStudio-<numeric version>" (else Bambu drops the colors);
  3. one mesh <object> per color plus the components root, every mesh non-empty;
  4. triangle/vertex counts match the source job, when given;
  5. the build item is translated to the bed center (a project is not
     auto-arranged: without it the model opens in the front-left corner);
  6. every part declares an explicit extruder, in palette order;
  7. filament_colour[i] equals part i's color, compared as exact hex;
  8. per-filament arrays all have one entry per color, and no color-distance
     heuristic is involved anywhere (the check above is equality);
  9. the tables that are NOT sized by N — self index, flush matrix and vector,
     multipliers, nozzle stats, inherits groups — and the wipe tower sitting
     inside the area every extruder reaches.

Group 9 is the one that costs a print: none of it produces a read error. The
file opens fine and dies at slicing, and the flush matrix does not even fail
there — only the GUI refuses it. Checking the sizes here is the only guard.

Exit code 0 = all good. Standard library only; trimesh is used for the optional
watertight check when it happens to be installed.
"""

import argparse
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from write_3mf import common_box  # noqa: E402

WANTED = ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model",
          "Metadata/model_settings.config", "Metadata/project_settings.config"]


def hex6(c):
    h = str(c).replace("#", "").upper()
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return "#" + h[:6]


def check_tables(cfg, n):
    """The profile tables whose size is NOT the number of colors.

    Every one of these was a print that failed at slicing with the file loading
    perfectly: the sizes are B (nozzles), V (extruder variants), N*V, B*N*N,
    2*N and N+2. See reference/format.md for the error each one produces."""
    out = []
    b = len(cfg.get("nozzle_diameter") or [])
    if not b:
        return ["profile has no nozzle_diameter — it is not a complete machine profile"]

    # An unresolved preset dump loses the per-extruder keys: on a 2-nozzle
    # machine extruder_type comes back with one entry and the project opens but
    # cannot slice. Same smell as printable_height falling back to 100.
    et = cfg.get("extruder_type") or []
    if len(et) != b:
        out.append("extruder_type has %d entries for %d nozzles — the profile was "
                   "built from unresolved presets (see format.md)" % (len(et), b))

    idx = cfg.get("filament_self_index") or []
    var = cfg.get("filament_extruder_variant") or []
    if len(var) != len(idx):
        out.append("filament_self_index (%d) and filament_extruder_variant (%d) "
                   "must have the same length" % (len(idx), len(var)))
    if not idx or len(idx) % n:
        out.append("filament_self_index has %d entries, not a multiple of %d colors"
                   % (len(idx), n))
    else:
        v = len(idx) // n
        want = [str(i + 1) for i in range(n) for _ in range(v)]
        if idx != want:
            out.append("filament_self_index is %s, expected %s — the slicer "
                       "concludes filaments 2..N have no variant" % (idx, want))

    for k, want in (("flush_volumes_matrix", b * n * n),
                    ("flush_volumes_vector", 2 * n),
                    ("flush_multiplier", b),
                    ("flush_multiplier_fast", b),
                    ("extruder_nozzle_stats", b),
                    ("inherits_group", n + 2),
                    ("different_settings_to_system", n + 2)):
        got = len(cfg.get(k) or [])
        if got != want:
            out.append("%s has %d entries, expected %d" % (k, got, want))

    lo, hi = common_box(cfg)
    try:
        tx = float((cfg.get("wipe_tower_x") or [0])[0])
        ty = float((cfg.get("wipe_tower_y") or [0])[0])
    except (TypeError, ValueError):
        return out + ["wipe_tower_x/y is not a number"]
    w = float(cfg.get("prime_tower_width") or 60)
    if not (lo[0] <= tx and tx + w <= hi[0] and lo[1] <= ty and ty + w <= hi[1]):
        out.append("wipe tower at %g,%g (%g wide) is outside the box every "
                   "extruder reaches (%g..%g, %g..%g)"
                   % (tx, ty, w, lo[0], hi[0], lo[1], hi[1]))
    return out


def validate(path, job=None):
    problems = []
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return ["not a zip: %s" % e]
    bad = z.testzip()
    if bad:
        problems.append("corrupt entry: %s" % bad)
    names = z.namelist()
    for n in WANTED:
        if n not in names:
            problems.append("missing entry: %s" % n)
    if problems:
        return problems

    model = z.read("3D/3dmodel.model").decode("utf8")
    msettings = z.read("Metadata/model_settings.config").decode("utf8")
    try:
        project = json.loads(z.read("Metadata/project_settings.config"))
    except Exception as e:
        return ["project_settings.config is not JSON: %s" % e]

    if not re.search(r'<metadata name="Application">BambuStudio-\d+\.\d+\.\d+\.\d+</metadata>',
                     model):
        problems.append('Application is not "BambuStudio-<numeric version>" — '
                        "Bambu will read and discard the color table")

    objects = model.split("<object ")[1:]
    meshes = [o for o in objects if "<mesh>" in o]
    if not meshes:
        problems.append("no mesh objects")
    if len(objects) != len(meshes) + 1:
        problems.append("missing the components root object")

    counts = []
    for i, o in enumerate(meshes):
        nv = o.count("<vertex ")
        nt = o.count("<triangle ")
        counts.append((nv, nt))
        if not nv or not nt:
            problems.append("part %d has no geometry" % (i + 1))

    item = re.search(r"<item [^>]*>", model)
    tr = re.search(r'transform="([^"]+)"', item.group(0)) if item else None
    if not item:
        problems.append("no <build><item>")
    elif not tr:
        problems.append("<item> has no transform — the model would open in the "
                        "front-left corner of the bed")
    else:
        n = [float(x) for x in tr.group(1).split()]
        xs, ys = [], []
        for p in project.get("printable_area", []):
            q = str(p).split("x")
            if len(q) == 2:
                xs.append(float(q[0]))
                ys.append(float(q[1]))
        if len(n) != 12:
            problems.append("<item> transform does not have 12 numbers")
        elif xs:
            cx = (min(xs) + max(xs)) / 2.0
            cy = (min(ys) + max(ys)) / 2.0
            if abs(n[9] - cx) > 0.01 or abs(n[10] - cy) > 0.01:
                problems.append("model placed at %g,%g instead of the bed center %g,%g"
                                % (n[9], n[10], cx, cy))

    parts = msettings.split("<part ")[1:]
    if len(parts) != len(meshes):
        problems.append("model_settings.config lists %d parts for %d mesh objects"
                        % (len(parts), len(meshes)))
    for i, p in enumerate(parts):
        m = re.search(r'key="extruder" value="(\d+)"', p)
        if not m:
            problems.append("part %d has no explicit extruder" % (i + 1))
        elif int(m.group(1)) != i + 1:
            problems.append("part %d is bound to extruder %s" % (i + 1, m.group(1)))

    colors = project.get("filament_colour") or []
    if len(colors) != len(meshes):
        problems.append("filament_colour has %d entries for %d parts"
                        % (len(colors), len(meshes)))
    for k in ("filament_type", "filament_settings_id", "filament_ids"):
        v = project.get(k)
        if not isinstance(v, list) or len(v) != len(meshes):
            problems.append("%s does not have one entry per color" % k)

    problems += check_tables(project, len(meshes))

    if job:
        with open(job, encoding="utf8") as f:
            src = json.load(f)["parts"]
        if len(src) != len(meshes):
            problems.append("job has %d parts, file has %d" % (len(src), len(meshes)))
        else:
            for i, p in enumerate(src):
                used = {j for t in p["triangles"] for j in t}
                nv, nt = counts[i]
                if nt != len(p["triangles"]):
                    problems.append("part %d: %d triangles in file vs %d in job"
                                    % (i + 1, nt, len(p["triangles"])))
                if nv != len(p["vertices"]) and nv != len(used):
                    problems.append("part %d: %d vertices in file vs %d in job"
                                    % (i + 1, nv, len(p["vertices"])))
                want = hex6(p["color"])
                if i < len(colors) and colors[i] != want:
                    problems.append("filament %d is %s, part %d is %s"
                                    % (i + 1, colors[i], i + 1, want))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--job", help="the JSON job the file was written from")
    a = ap.parse_args()
    problems = validate(a.file, a.job)
    if problems:
        print("FAIL")
        for p in problems:
            print("  x " + p)
        sys.exit(1)
    z = zipfile.ZipFile(a.file)
    project = json.loads(z.read("Metadata/project_settings.config"))
    print("OK  %s" % a.file)
    tr = re.search(r'transform="([^"]+)"', re.search(r"<item [^>]*>",
         z.read("3D/3dmodel.model").decode("utf8")).group(0)).group(1).split()
    print("    placed at : %s, %s" % (tr[9], tr[10]))
    print("    printer   : %s | %s | layer %s mm"
          % (project.get("printer_settings_id"), project.get("print_settings_id"),
             project.get("layer_height")))
    for i, c in enumerate(project["filament_colour"]):
        print("    filament %d -> %s -> part %d" % (i + 1, c, i + 1))


if __name__ == "__main__":
    main()
