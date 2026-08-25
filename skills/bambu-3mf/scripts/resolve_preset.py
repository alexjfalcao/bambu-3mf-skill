#!/usr/bin/env python3
"""Flatten a Bambu system preset into a standalone one.

    python3 resolve_preset.py machine "Bambu Lab H2C 0.4 nozzle" maq.json
    python3 resolve_preset.py process "0.20mm Standard @BBL H2C"  proc.json
    python3 resolve_preset.py filament "Bambu PLA Basic @BBL H2C" fil.json

Why this exists: a system preset only carries the keys that OVERRIDE its
`inherits` parent, and its gcode blocks arrive through `include`. Hand the raw
file to the Bambu CLI and everything not literally in it falls back to the
Slic3r defaults — which is how a template ends up with printable_height 100 on
a machine that is 325 tall, one `extruder_type` entry for two extruders and a
single nozzle variant instead of three. Bambu opens that project without a
word and then cannot slice it.

Resolution order, matching the slicer: parent chain first, then every
`include`, then the file's own keys. `type`/`name`/`from`/`instantiation`/
`setting_id` are bookkeeping and are dropped from the parents.
"""

import json
import os
import sys

DATADIR = os.path.expanduser("~/Library/Application Support/BambuStudio")
DROP = ("inherits", "include", "from", "instantiation", "setting_id", "type")


def load(kind, name, root):
    path = os.path.join(root, kind, name + ".json")
    if not os.path.exists(path):     # parents and includes may live in any kind dir
        for other in ("machine", "process", "filament"):
            p = os.path.join(root, other, name + ".json")
            if os.path.exists(p):
                path = p
                break
        else:
            raise SystemExit("preset not found: %s" % name)
    with open(path, encoding="utf8") as f:
        return json.load(f)


def resolve(kind, name, root, seen=None):
    seen = seen or set()
    if name in seen:
        raise SystemExit("inherits cycle at %s" % name)
    seen.add(name)
    raw = load(kind, name, root)
    cfg = {}
    if raw.get("inherits"):
        cfg.update(resolve(kind, raw["inherits"], root, seen))
    for inc in raw.get("include") or []:
        block = load(kind, inc, root)
        cfg.update({k: v for k, v in block.items() if k not in DROP and k != "name"})
    cfg.update({k: v for k, v in raw.items() if k not in ("inherits", "include")})
    for k in ("from", "instantiation", "setting_id"):
        cfg.pop(k, None)
    return cfg


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    kind, name, out = sys.argv[1], sys.argv[2], sys.argv[3]
    root = os.path.join(os.environ.get("BBL_DATADIR", DATADIR), "system", "BBL")
    cfg = resolve(kind, name, root)
    with open(out, "w", encoding="utf8") as f:
        json.dump(cfg, f, indent=1, sort_keys=True)
    print("%s  %d keys -> %s" % (name, len(cfg), out))


if __name__ == "__main__":
    main()
