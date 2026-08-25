# bambu-3mf

A [Claude Code](https://claude.com/claude-code) skill that writes **3MF files Bambu Studio
opens with every colour already on the right extruder** — no Color Count dialog, no grey
import, no "the colours came out swapped", no `The 3mf file has invalid config` when you
hit Slice.

Ask Claude for a two-colour coaster, a nameplate, an inlay — or hand it a mesh you already
have — and you get a `.3mf` that loads as a **project**, centred on the plate, sliceable
as-is.

---

## Why this exists

A 3MF a slicer colours correctly is not "a mesh with colours". It is a small project with
three links that all have to be in the file:

```
colour of the design
   -> part i         one closed solid per colour, in 3D/3dmodel.model
   -> extruder i+1   Metadata/model_settings.config
   -> filament i+1   Metadata/project_settings.config  (filament_colour[i])
```

Miss the last link and the geometry still loads — every part just takes the colour of
whatever filament happens to sit in that slot. And that project config has to be a
**complete, correctly sized** profile: half the ways to get it wrong load without a single
complaint and fail only at Slice time.

Every rule in this skill came out of a real plate on a Bambu Lab H2C, not from reading the
3MF spec. A few of the findings:

- `<metadata name="Application">` must be `BambuStudio-<numeric version>`. Any other value
  and Bambu parses `project_settings.config` and throws it away.
- `<basematerials>` / `pindex` is decorative — six objects using it all landed on
  extruder 1.
- Coordinates need 5 decimals; with 3, neighbouring vertices on fine meshes collapse into
  degenerate triangles.
- Colours are matched as **exact hex**. No tolerance, no clustering, no nearest-filament.
- A project is never auto-arranged, so a model built around the origin opens in the
  front-left corner, mostly off the plate.
- Replicating a profile per colour is not "repeat every length-1 array":
  `flush_volumes_matrix` is `nozzles × N²`, `flush_volumes_vector` is `2N`,
  `inherits_group` is `N+2`, and which keys are per-filament cannot be guessed from the
  name — `nozzle_temperature` is, `wipe_tower_x` is not.

The full list, with what was tested and what silently fails, is in
[`skills/bambu-3mf/reference/format.md`](skills/bambu-3mf/reference/format.md).

---

## Install

As a plugin, from inside Claude Code:

```
/plugin marketplace add alexjfalcao/bambu-3mf-skill
/plugin install bambu-3mf@alexjfalcao
```

Or drop the skill straight into your skills folder:

```bash
git clone https://github.com/alexjfalcao/bambu-3mf-skill
cp -R bambu-3mf-skill/skills/bambu-3mf ~/.claude/skills/
```

Python 3.9+, standard library only — no dependencies.

---

## Use

Mostly you just ask, and Claude loads the skill on its own:

> *"make me a 60 mm coaster with a yellow rim on a purple base, for the A1"*

The scripts are plain CLI tools too:

```bash
S=~/.claude/skills/bambu-3mf/scripts   # or <plugin>/skills/bambu-3mf/scripts

# 1. build the parts (or produce vertices/triangles any other way)
python3 - "$S" <<'PY'
import sys, json
sys.path.insert(0, sys.argv[1])
import solids
tol  = 0.10                                  # = the quality level's tolerance
base = solids.cylinder(40, 2, tol)
rim   = solids.ring(38, 40, 4, tol, z0=2)
json.dump({"parts": [
  {"name": "base", "color": "#5B2A7A", "vertices": base[0], "triangles": base[1]},
  {"name": "rim",  "color": "#F2D02C", "vertices": rim[0],  "triangles": rim[1]},
]}, open("job.json", "w"))
PY

# 2. write the 3MF
python3 $S/write_3mf.py job.json coaster.3mf --quality good --printer h2c

# 3. validate before handing it over
python3 $S/validate_3mf.py coaster.3mf --job job.json
```

`write_3mf.py` also imports: `write_3mf(parts, path, name=..., quality=..., printer=...)`.

### Quality levels

| quality | chord tolerance | layer height | triangle budget | use for |
|---|---|---|---|---|
| `test` | 0.30 mm | 0.24 mm | 200 k | shape checks, fit tests, fast iteration |
| `good` | 0.10 mm | 0.20 mm | 1.5 M | the default for real prints |
| `extrafine` | 0.03 mm | 0.08 mm | 6 M | fine relief, thin inlays, jewelry-scale detail |

### Printers

`--printer h2c | a1 | p1s | x1c`

| | bed | height | nozzles |
|---|---|---|---|
| `h2c` | 330 × 320 | 325 | 2 |
| `a1` | 256 × 256 | 256 | 1 |
| `p1s` | 256 × 256 | 250 | 1 |
| `x1c` | 256 × 256 | 250 | 1 |

Opening a file on some other Bambu machine still works — the slicer substitutes presets and
keeps the colours — but the embedded gcode belongs to the profile, so send the right one.
Adding a machine is a recipe in `reference/format.md`.

---

## What's in the box

```
skills/bambu-3mf/
├── SKILL.md                        the skill itself: the chain, the hard rules
├── scripts/
│   ├── write_3mf.py                writer + quality table + machine profiles; CLI or import
│   ├── validate_3mf.py             structural + profile-sizing checks, exit 1 on failure
│   ├── solids.py                   watertight primitives: box, cylinder, ring, extrude, revolve, audit
│   └── resolve_preset.py           flattens a system preset (inherits + include) for regeneration
├── assets/
│   ├── project_template.json       resolved H2C profile, 567 keys, dumped from Bambu itself
│   ├── machine_deltas.json         A1 / P1S / X1C as the ~200 keys that differ
│   └── per_filament_keys.json      the 145 keys replicated per colour
└── reference/
    ├── format.md                   package anatomy, every gotcha, how to retarget the printer
    └── validation.md               validation recipes, including slicing through the Bambu CLI
```

Bring your own geometry if you like — the writer only wants `vertices` + `triangles` per
colour, so trimesh, numpy-stl, marching cubes or a CAD export all work. Two things stay
yours: split by colour (one solid per colour, no shared volume) and keep each part
manifold (`solids.audit()` must report `open_edges == 0`).

---

## Verified against

BambuStudio 02.08.02.61 (macOS), sliced on H2C and A1 profiles.

---

## Credits

Written and measured by **Alex Falcão**.

- Site: <https://www.alexfalcao.pro.br>
- Models: [MakerWorld @alexjfalcao](https://makerworld.com/pt/@alexjfalcao) ·
  [Cults3D /alexjfalcao](https://cults3d.com/pt/usuarios/alexjfalcao)
- Code: [GitHub @alexjfalcao](https://github.com/alexjfalcao)

The generator that forced most of these findings is **[Mandala
Forge](https://www.alexfalcao.pro.br/mandala/)** ([repo](https://github.com/alexjfalcao/mandala-forge)):
cloisonné mandalas for 3D printing, exported straight to a Bambu project with the colours
already on the right extruders.

MIT licensed.
