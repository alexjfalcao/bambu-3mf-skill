# The package, and every trap in it

Everything here was measured against BambuStudio 02.08.02.61 on macOS by writing
a file, re-exporting it through the Bambu CLI, and reading what came back — plus,
where the CLI could not answer, by opening the file in the GUI and sampling the
filament swatches on screen.

## Anatomy

```
[Content_Types].xml
_rels/.rels
3D/3dmodel.model                  Application + <basematerials> + one <object>
                                  per color + a root <object> with <components>
Metadata/model_settings.config    part i -> extruder i+1          (XML)
Metadata/project_settings.config  filament i+1 -> color of part i (JSON)
```

Assets behind it: `project_template.json` (the resolved H2C profile),
`machine_deltas.json` (A1 / P1S / X1C) and `per_filament_keys.json` (the 145
keys that get replicated per color).

Object ids: meshes are `2 .. 1+N`, the components root is `2+N`, and the build
item points at the root. `<part id>` in `model_settings.config` must repeat the
mesh object id, and `p1`/`pindex` are 0-based.

## What actually decides the color

| `<metadata name="Application">` | `project_settings.config` | result |
|---|---|---|
| anything not starting with `BambuStudio-` | absent, partial or complete | config **ignored**; each part shows the color of the user's slot i |
| `BambuStudio-<numeric version>` | filament keys only | JSON parsed ("load project config file successfully" in the log) and then **discarded** when presets are rebuilt |
| `BambuStudio-<numeric version>` | **complete** | adopted: `filament_colour` becomes the palette, parts sit on extruders 1..N |
| `BambuStudio-<non numeric>` | complete | load aborts, nothing opens |

So both halves are required, and there is no lightweight middle ground. The
"colors are swapped" bug is entirely explained by row 1: a project with 8 loaded
filaments (`#161616`, `#65377B`, …) painted part 1 black because the file never
said part 1 was `#5B2A7A`.

## Failure modes that look like success

- **Silent CLI failure.** A rejected project produces no output file, prints
  nothing but a trace line, and still writes `"return_code": 0` into
  `result.json` in the working directory. Check for the output file, never the
  exit code. (That `result.json` lands in your CWD — clean it up.)
- **`The 3mf file has invalid config, load geometry data only`.** Fires on any
  3MF without `project_settings.config`, including files from Fusion and
  Blender. Cosmetic for geometry: `model_settings.config` is still read. Once
  the project config is present, the warning is gone.
- **OBJ colors going through `Color Count`.** Importing a colored OBJ opens
  *Import Model* with an auto-chosen cluster count; 6 colors collapsing into 2
  shows up as *Filament Mapping* listing colors that exist nowhere in the file —
  they are centroids. A proper 3MF project skips that dialog entirely.
- **`basematerials` looking right in a viewer.** Generic 3MF viewers honor it,
  slicers do not.

## Regenerating `assets/project_template.json`

**Resolve the presets first.** A system preset carries only the keys that
override its `inherits` parent, and its gcode blocks arrive through `include`.
Hand the raw file to the CLI and everything not literally in it falls back to
the *Slic3r* defaults. That is how the first version of this template ended up
with `printable_height: "100"` on a machine that is 325 tall,
`extruder_type: ["Direct Drive"]` for a two-extruder machine, one nozzle
variant instead of three and a generic `machine_start_gcode` — a profile Bambu
opens without a word and then cannot slice.

```bash
S="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}/skills/bambu-3mf/scripts"
python3 $S/resolve_preset.py machine  "Bambu Lab H2C 0.4 nozzle"  maq.json
python3 $S/resolve_preset.py process  "0.20mm Standard @BBL H2C"  proc.json
python3 $S/resolve_preset.py filament "Bambu PLA Basic @BBL H2C"  fil.json

D="$HOME/Library/Application Support/BambuStudio"
BS="/Applications/3D Software/BambuStudio.app/Contents/MacOS/BambuStudio"
"$BS" --datadir "$D" --load-settings "maq.json;proc.json" --load-filaments fil.json \
      --export-3mf mold.3mf --outputdir "$PWD/out" any_model.stl
# Metadata/project_settings.config of mold.3mf is the new template
```

Then trim every per-filament array back to ONE entry (the writer replicates
them) and set `filament_ids` to `GFA00` / `filament_colour` to `#FFFFFF`.

Sanity-check the dump before trusting it — these are the tells of an
unresolved parent chain:

| key | H2C, resolved | unresolved |
|---|---|---|
| `printable_height` | `325` | `100` |
| `extruder_type` | 2 entries | 1 |
| `filament_extruder_variant` | 3 (Standard / High Flow / E3D High Flow) | 1 |
| `machine_start_gcode` | ~17 kB | generic stub |

`--load-filaments` is mandatory too: without it the dump lacks 21 keys
(`filament_settings_id`, `filament_retraction_length`, `filament_colour_type`, …)
and Bambu refuses the project. `--datadir` is what lets the CLI resolve presets
at all; without it, it ignores an embedded project config entirely.

Machine-specific keys in the template (`printer_settings_id`, `printable_area`,
`nozzle_diameter`, gcode blocks) are what makes it "complete".

## One filament in the template, N in the project

The template describes **one** filament, and that filament has **V** extruder
variants (`filament_extruder_variant`; V = 3 on the H2C). Replicating it for N
colors is not "repeat every length-1 array" — several tables are sized by
something else entirely, and **none of them produces a read error**. The file
opens perfectly and dies at slicing.

| table | correct size for N colors | what a wrong one does |
|---|---|---|
| the 145 keys in `per_filament_keys.json` | template block (1 or V entries) repeated N× | missing keys leave filaments 2..N on defaults |
| `filament_self_index` | `1×V, 2×V, … N×V` | all `"1"` → *"could not found extruder_type Direct Drive, nozzle_volume_type Standard, filament_index 2"*, then *"No valid nozzle found."* |
| `flush_volumes_matrix` | one N×N block **per nozzle**: `nozzles × N × N` | *"Flush volumes matrix do not match to the correct size!"* |
| `flush_volumes_vector` | `2 × N` | same family |
| `flush_multiplier`, `flush_multiplier_fast` | one entry per nozzle | same |
| `extruder_nozzle_stats` | `"<type>#<slots>"` per extruder | empty → *"No valid nozzle found."* |
| `inherits_group`, `different_settings_to_system` | `N + 2` (process + N filaments + machine) | preset bundle rebuilt wrong |
| `wipe_tower_x` / `_y` | inside the box every extruder reaches | *"Found G-code in unprintable area of multi-extruder printers"* |

The per-filament key list was built **empirically**, by diffing a 1-filament
dump against a real 8-filament project and keeping every key whose array grows
exactly ×8. It cannot be derived from the name: `nozzle_temperature` is per
filament and `wipe_tower_x` is not, and neither starts with `filament`.

The matrix size was measured across 13 projects written by Bambu itself, with
no exception: P1S N=5 (1 nozzle) → 25; H2C N=5 (2 nozzles) → 50; H2C N=1 → 2;
H2D N=2 → 8; A1 N=2 → 4; N=6 on one nozzle → 36. Consecutive N×N blocks, each
with a zero diagonal.

In the GUI the same broken profile surfaces as **"Wipe tower generation failed,
possibly due to empty first layer"** — the slicer accepts the project, fixes
what it can, and reaches the tower with no valid purge volume.

## The wipe tower and the common box

On a two-nozzle machine each extruder reaches a different rectangle: on an H2C
extruder 1 spans x=0..325 and extruder 2 x=25..330 (`extruder_printable_area`).
What matters for anything you place yourself is the **intersection**. The
template ships the slicer's default `wipe_tower_x: 15`, which the second
extruder cannot reach at all.

`write_3mf.wipe_tower()` puts the tower behind the model's bounding box,
centered in x, clamped into that intersection with 2 mm of margin. For an
object so large that nothing fits, it clamps as close as it can and lets the
slicer complain.

## Other machines: deltas, not dumps

`assets/machine_deltas.json` holds A1, P1S and X1C as the ~200 keys that differ
from the H2C, and `write_3mf.profile()` applies one over the template. Deltas
because the three are **single-extruder**: their "per extruder" and "per
variant" arrays change length, and swapping the whole key handles that with no
logic. `project_config` reads `nozzles = len(nozzle_diameter)`, so the flush
matrix drops from `2N²` to `N²` on its own.

The ~68 kB of gcode cannot be omitted — whoever opens the project slices with
the gcode inside it, and sending an H2C start sequence to an A1 means a crash
into the bed.

Preset names do not follow a pattern. The X1C is `Bambu Lab X1 Carbon 0.4
nozzle`, and the P1S has no process of its own: it uses `0.20mm Standard @BBL
X1C`, the only one listing `Bambu Lab P1S 0.4 nozzle` in `compatible_printers`.
Passing `@BBL P1P` fails with *"process not compatible with printer"*.

| | bed | height | nozzles | flush matrix (N=6) |
|---|---|---|---|---|
| H2C | 330 × 320 | 325 | 2 | 72 |
| A1 | 256 × 256 | 256 | 1 | 36 |
| P1S | 256 × 256 | 250 | 1 | 36 |
| X1 Carbon | 256 × 256 | 250 | 1 | 36 |

## Model XML details that bite

- Write coordinates with **5 decimals**. Three decimals merged distinct vertices
  on fine meshes into degenerate triangles.
- Each part is one closed solid. Parts that touch face to face are normal for
  multi-material; after a merge those shared walls become edges with 4 faces, so
  `trimesh.is_watertight` on the *union* can read False while every part is
  perfectly closed. Audit part by part.
- Keep the components root: a flat list of top-level objects loads as separate
  printable objects instead of one multi-material object.
- **Place the object yourself.** A plain model import gets auto-arranged; a
  project does not. `<item objectid="N"/>` with no transform drops the model at
  bed coordinate (0,0) — the front-left corner — which is what "it opens off the
  plate" looks like. Emit
  `transform="1 0 0 0 1 0 0 0 1 <cx> <cy> 0" printable="1"`, with the last three
  numbers being the translation and `cx`/`cy` the center of `printable_area`
  (165, 160 on an H2C). Bambu writes exactly this in its own projects.
- `<metadata name="Designer">` is a good place for your tool's name, since
  `Application` is spoken for.

## Layer height and quality

`layer_height` (and `initial_layer_print_height`) are stamped by the writer from
the quality level. They ride along as project overrides on top of the process
preset named in the template, so Bambu shows the preset as modified — that is
expected and honest. Everything else about the process is left alone.
