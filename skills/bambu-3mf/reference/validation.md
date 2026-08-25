# Validating a 3MF

Four levels, cheapest first. Do the first two always; slice with the CLI
whenever the file is going to a real printer, and open it in the GUI once per
template change.

## 1. Structural — `validate_3mf.py`

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-$HOME/.claude}"/skills/bambu-3mf/scripts/validate_3mf.py out.3mf --job job.json
```

Checks the zip and the 5 entries, the `Application` trigger, one non-empty mesh
object per color plus the components root, triangle/vertex counts against the
source job (geometry must be untouched by the export), an explicit extruder per
part in palette order, and `filament_colour[i]` equal to part i's color as exact
hex.

It also checks the tables that are **not** sized by N — `filament_self_index`
(`N×V`), `flush_volumes_matrix` (`nozzles × N²`), `flush_volumes_vector` (`2N`),
the multipliers and `extruder_nozzle_stats` (one per nozzle), `inherits_group`
and `different_settings_to_system` (`N+2`) — plus the wipe tower sitting inside
the box every extruder reaches, and `extruder_type` having one entry per nozzle
(the tell of a profile built from unresolved presets). That group is the one
worth having a script for: none of it produces a read error, so the file looks
fine right up to the failed print.

Exit 1 on any failure, with one line per problem.

Prove it has teeth once, on any change to the writer: reverse the color list or
put back a non-Bambu `Application` string, and confirm it fails.

## 2. Geometry — `solids.audit()` or trimesh

```python
from solids import audit
audit(v, t)            # {'open_edges': 0, 'degenerate': 0, ...}
```

`open_edges` must be 0 for every part **individually**. With trimesh installed:

```python
m = trimesh.Trimesh(vertices=v, faces=t, process=False)
m.is_watertight and m.is_winding_consistent and m.volume > 0
```

`volume > 0` is the outward-normals check — a negative volume means the part
slices inside out. Do not run these on the merged model: parts touching face to
face produce 4-face edges after a merge and read as non-watertight while each
part is fine.

## 3. End to end — the Bambu CLI, with `--slice`

The slicer is the only authority. **Slice**, do not just re-export: a profile
the slicer cannot use passes `--info` and `--export-3mf` without a word.

```bash
D="$HOME/Library/Application Support/BambuStudio"
BS="/Applications/3D Software/BambuStudio.app/Contents/MacOS/BambuStudio"
mkdir -p "$PWD/out"
"$BS" --datadir "$D" --slice 0 --outputdir "$PWD/out" "$PWD/out.3mf" 2>&1 | tee slice.log
```

Paths must be absolute, and `result.json` lands in `--outputdir`. What good
looks like, measured on a 6-color disc:

```python
import json
r = json.load(open('out/result.json'))
r['return_code'] == 0 and r['error_string'] == 'Success.'
len(r['sliced_plates'][0]['filaments']) == 6          # one per color
r['sliced_plates'][0]['feature_type_times']['Prime tower'] > 0
```

**`return_code: 0` is not enough.** A file with `filament_self_index` all `"1"`
sliced with `return_code: 0` / `"Success."` and still wrote gcode — while the
log carried five lines of

```
could not found extruder_type Direct Drive, nozzle_volume_type Standard,
filament_index 2, extruder index 1
```

So grep the log, and know the benign noise: three `[error] Invalid T command
(T1001/T65535/T65279)` lines come from Bambu's own `change_filament_gcode` and
appear on perfectly good files.

```bash
grep -E "could not found extruder_type|No valid nozzle|Flush volumes|unprintable area" slice.log
```

A wrong `flush_volumes_matrix` **does** fail the CLI on 02.08.02.61
(`return_code: -100`, "Flush volumes matrix do not match to the correct size!"),
both when it is `N²` instead of `nozzles × N²` and when it is the template's
stale 4×4. Do not rely on that alone — `validate_3mf.py` checks the size
statically, which is cheaper and version-proof.

If no output appears at all and the log says nothing: the project was rejected
outright. Bambu's logs are encrypted, so bisect the config.

### Reading back what the slicer understood

```bash
"$BS" --datadir "$D" --export-3mf "$PWD/out/back.3mf" --outputdir "$PWD/out" "$PWD/out.3mf"
```

```python
import json, re, zipfile
z = zipfile.ZipFile('out/back.3mf')
cfg = json.loads(z.read('Metadata/project_settings.config'))
ms = z.read('Metadata/model_settings.config').decode()
print(cfg['filament_colour'], cfg['layer_height'])
print(re.findall(r'key="extruder" value="(\d+)"', ms)[1:])   # 1,2,3,... ([0] is the root)
print(re.findall(r'mesh_stat face_count="(\d+)"', ms))       # identical to what you wrote
print(set(re.findall(r'edges_fixed="(\d+)"', ms)))           # {'0'}
```

Anything other than `{'0'}` for `edges_fixed` / `facets_reversed` means the
slicer had to repair your mesh. Note the CLI applies its own printer settings on
a plain model import, so `printer_settings_id` coming back proves nothing about
your template; the color table, the extruder binding and the geometry do.

## 3b. Color, read by a 3MF library

```bash
pip install lib3mf
```

**Do not use trimesh for this.** Its 3MF reader ignores materials and returns
everything grey — a guaranteed false negative on exactly the thing you are
checking. trimesh is for geometry (`is_watertight`, `is_winding_consistent`,
`volume`), lib3mf is for color.

## 4. The eyeball check

Open it in Bambu Studio: the *Project Filaments* panel should list one filament
per color, in order, with your swatches, and the plate should show the object in
those colors. This is the only check that covers the GUI's own project-loading
path — worth doing once per template change.
