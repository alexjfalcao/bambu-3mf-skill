---
name: bambu-3mf
description: Build 3D objects and export them as 3MF files that Bambu Studio opens with the right colors on the right parts — one closed solid per color, each bound to its own filament and extruder, at test / good / extrafine quality. Use when asked to create, model, or export a 3D object for printing, to write or fix a 3MF (multicolor or single color), when colors land on the wrong filament or come in grey in Bambu Studio / PrusaSlicer, when a slicer shows "The 3mf file has invalid config", when a Color Count or Import Model dialog is clustering colors, or when working with AMS / multi-material assignment.
license: MIT
metadata:
  version: "1.1"
  author: Alex Falcão
  homepage: https://www.alexfalcao.pro.br
  verified_against: BambuStudio 02.08.02.61 (macOS); sliced on H2C and A1 profiles
---

# Bambu-compatible 3MF

A 3MF that a slicer colors correctly is not "a mesh with colors". It is a small
project: one closed solid per color, a part→extruder table, and a filament→color
table. Miss the last one and the geometry still loads, but every part takes the
color of whatever filament sits in that slot — the classic "colors are swapped".

## The chain

```
color of the design
   -> part i            one closed solid per color, in 3D/3dmodel.model
   -> extruder i+1      Metadata/model_settings.config
   -> filament i+1      Metadata/project_settings.config  (filament_colour[i])
```

And that project config has to be a **complete, correctly sized** profile: half
the ways to get it wrong load without a single complaint and fail only when the
user hits Slice.

All three links must be in the file. Never rely on `<basematerials>`/`pindex`
(Bambu ignores it), on per-triangle colors, or on any nearest-color matching.

## Quick start

```bash
S="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/bambu-3mf/scripts"

# 1. build parts (or produce vertices/triangles any other way)
python3 - "$S" <<'PY'
import sys, json
sys.path.insert(0, sys.argv[1])
import solids
tol = 0.10                                   # = the quality level's tolerance
base = solids.cylinder(40, 2, tol)
rim  = solids.ring(38, 40, 4, tol, z0=2)
json.dump({"parts": [
  {"name": "base", "color": "#5B2A7A", "vertices": base[0], "triangles": base[1]},
  {"name": "rim",  "color": "#F2D02C", "vertices": rim[0],  "triangles": rim[1]},
]}, open("job.json", "w"))
PY

# 2. write the 3MF  (--printer h2c | a1 | p1s | x1c, default h2c)
python3 $S/write_3mf.py job.json coaster.3mf --quality good --printer h2c

# 3. validate before handing it over
python3 $S/validate_3mf.py coaster.3mf --job job.json
```

Or import the writer: `write_3mf(parts, path, name=..., quality=..., printer=...)`.

## Quality levels

`quality` sets the mesh tolerance you should tessellate with and the layer
height stamped into the project. Pick by what the object is for, not by habit —
extrafine multiplies triangles and slicing time and only pays off on curved
surfaces smaller than a few mm.

| quality | chord tolerance | layer height | triangle budget | use for |
|---|---|---|---|---|
| `test` | 0.30 mm | 0.24 mm | 200 k | shape checks, fit tests, fast iteration |
| `good` | 0.10 mm | 0.20 mm | 1.5 M | the default for real prints |
| `extrafine` | 0.03 mm | 0.08 mm | 6 M | fine relief, thin inlays, jewelry-scale detail |

`solids.segments_for(radius, tol)` turns the tolerance into a segment count, so
a 40 mm circle is 24 segments at `test` and 82 at `extrafine`. The writer warns
when a part set blows the budget; over ~2 M triangles Bambu Studio gets sluggish
and the 3MF stops being a comfortable file to hand around.

## Hard rules (measured, not guessed)

1. **`<metadata name="Application">` must be `BambuStudio-<numeric version>`.**
   With any other value Bambu parses `project_settings.config` and throws it
   away — the colors revert to the user's slots. A non-numeric suffix
   (`BambuStudio-MyTool`) makes it abort the load entirely.
2. **`project_settings.config` must be a complete profile.** A config carrying
   only `filament_colour`/`filament_type` is accepted by the parser and ignored
   when the preset bundle is rebuilt. That is why the skill ships
   `assets/project_template.json` (567 keys, dumped from Bambu itself).
3. **One closed solid per color.** Each part must be watertight on its own:
   `solids.audit()` must report `open_edges == 0`. Parts may touch face to face;
   they must not interpenetrate.
4. **`<basematerials>` is decorative.** Six independent objects with
   `pid`/`pindex` and no `model_settings.config` all landed on extruder 1.
5. **Coordinates with 5 decimals.** With 3, neighbouring vertices on fine meshes
   collapse into degenerate triangles.
6. **Colors are compared as exact hex.** No tolerance, no clustering, no
   "closest filament". If a color must change, change it in the part list.
7. **A project is not auto-arranged.** Bambu places the object exactly where the
   `<build><item>` transform says, so a model built around the origin opens in
   the front-left corner, mostly off the plate. The writer translates the item to
   the bed center taken from the profile's `printable_area` — model wherever is
   convenient and let it place the object.
8. **Replicating the profile per color is not "repeat every length-1 array".**
   `filament_self_index` is `1×V, 2×V, …`; `flush_volumes_matrix` is
   `nozzles × N²`; `flush_volumes_vector` is `2N`; the multipliers and
   `extruder_nozzle_stats` are one per nozzle; `inherits_group` and
   `different_settings_to_system` are `N+2`. Which keys are per filament cannot
   be guessed from the name — `nozzle_temperature` is, `wipe_tower_x` is not,
   and neither starts with `filament`. The full list lives in
   `assets/per_filament_keys.json`. None of these errors is a read error: the
   file opens and the slice fails.
9. **The template must come from RESOLVED presets.** System presets carry only
   what overrides their `inherits` parent, with gcode arriving via `include`.
   Feed the raw file to the CLI and the rest falls back to Slic3r defaults —
   `printable_height: "100"` on a 325 mm machine, one `extruder_type` for two
   extruders. Use `scripts/resolve_preset.py`.
10. **The wipe tower belongs in the box every extruder reaches**, which on a
   two-nozzle machine is the intersection of `extruder_printable_area`, not the
   bed. The stock `wipe_tower_x: 15` is unreachable by the H2C's second extruder.

## Files

```
scripts/write_3mf.py     writer + QUALITY table + machine profiles; CLI or import
scripts/validate_3mf.py  structural + profile-sizing checks, exit 1 on failure
scripts/solids.py        watertight primitives: box, cylinder, ring, extrude, revolve, audit
scripts/resolve_preset.py  flattens a system preset (inherits + include) for regeneration
assets/project_template.json  resolved H2C profile, one entry per filament array
assets/machine_deltas.json    A1 / P1S / X1C as the ~200 keys that differ
assets/per_filament_keys.json the 145 keys replicated per color
reference/format.md      package anatomy, every gotcha, how to retarget the printer
reference/validation.md  validation recipes, including slicing through the Bambu CLI
```

Read `reference/format.md` before changing anything about the XML or the config
files — it records what was tested and what silently fails.

## When the object is not made of primitives

The writer only cares about `vertices` + `triangles` per color, so any source
works: an existing mesh library (trimesh, numpy-stl), marching squares/cubes over
a field, a CAD export. Two things stay your responsibility:

- **split by color yourself** — one solid per color, no shared volume;
- **keep each part manifold** — run `solids.audit()`, or `trimesh`
  `is_watertight` / `is_winding_consistent` if it is available.

Winding is counter-clockwise seen from outside (positive volume). A part with
inverted normals loads, then slices inside out.

## Other printers

`--printer h2c | a1 | p1s | x1c`. The template is the H2C (0.4 nozzle, 0.20mm
Standard, PLA Basic); the other three ride on top as deltas, because they are
single-extruder and their per-extruder arrays change length. The flush matrix
follows `nozzles` on its own, so `a1` gets `N²` where `h2c` gets `2N²`.

| | bed | height | nozzles |
|---|---|---|---|
| `h2c` | 330 × 320 | 325 | 2 |
| `a1` | 256 × 256 | 256 | 1 |
| `p1s` | 256 × 256 | 250 | 1 |
| `x1c` | 256 × 256 | 250 | 1 |

Opening a file on some other Bambu machine still works — the slicer substitutes
presets and keeps the colors — but the ~68 kB of embedded gcode is the profile's,
so send the right one. To add a machine, regenerate with `resolve_preset.py` +
the CLI (recipe in `reference/format.md`).

## Spending less filament on a multicolor plate

Every region extruded from z=0 carries its color through the whole part. Slicing
the object into a one-color base slab plus the design extruded on top of it cuts
both the colored filament and the purge: on a 120 mm mandala, 31.9 cm³ of colored
filament became 8.9 cm³. Give the base its own part in one color and start the
colored parts at the top of it.

## Credits

Written and measured by **Alex Falcão** — every rule here came out of a real
plate on a Bambu Lab H2C, not from the 3MF spec.

- Site: https://www.alexfalcao.pro.br
- Models: [MakerWorld @alexjfalcao](https://makerworld.com/pt/@alexjfalcao) ·
  [Cults3D /alexjfalcao](https://cults3d.com/pt/usuarios/alexjfalcao)
- Code: [GitHub @alexjfalcao](https://github.com/alexjfalcao)

The generator that forced most of these findings is **Mandala Forge**
(https://www.alexfalcao.pro.br/mandala/): cloisonné mandalas for 3D printing,
exported straight to a Bambu project with the colors already on the right
extruders.
