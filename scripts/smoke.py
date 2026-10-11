#!/usr/bin/env python3
"""Loads packages into RustGIS and runs them on the World Atlas sample.

    python scripts/smoke.py [--cli path/to/rustgis-cli]         # the packages this build made
    python scripts/smoke.py --all [--cli path/to/rustgis-cli]   # every listed package (nightly)

For each plugin: RustGIS must load it and list its commands; each tool runs with its defaults
(layer parameters get a sample layer of the right kind) and must succeed; each command that
needs no parameters runs too (a failure there is reported but doesn't fail the check, since a
command may need a selection or a layer the sample doesn't have).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
# A sample layer for each layer kind, and a field of it.
SAMPLE = {"polygonLayer": "Countries", "pointLayer": "Cities", "vectorLayer": "Countries", "layer": "Countries"}
FIELDS = {"field": "name", "numericField": "pop_est"}


def run(cli: str, package: Path, cmds: list[str], plugins_dir: str) -> subprocess.CompletedProcess:
    args = [cli, "run", "--no-plugins", "--plugin", str(package), "--sample"]
    for c in cmds:
        args += ["--cmd", c]
    env = dict(os.environ, RUSTGIS_PLUGINS_DIR=plugins_dir)
    return subprocess.run(args, capture_output=True, text=True, timeout=600, env=env)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=os.environ.get("RUSTGIS_CLI") or shutil.which("rustgis-cli") or "rustgis-cli")
    ap.add_argument("--all", action="store_true", help="every listed package, downloading published ones")
    args = ap.parse_args()
    registry = json.loads((DIST / "registry.json").read_text())
    failures = 0
    with tempfile.TemporaryDirectory() as plugins_dir, tempfile.TemporaryDirectory() as downloads:
        for e in registry["plugins"]:
            # Built by this run, or (with --all) downloaded from the bucket.
            file_name = e["package"].rsplit("/", 1)[-1]
            package = DIST / "packages" / e["id"] / file_name
            if not package.is_file():
                if not args.all:
                    continue
                package = Path(downloads) / file_name
                req = urllib.request.Request(e["package"], headers={"User-Agent": "rustgis-plugins-smoke"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    package.write_bytes(r.read())
            print(f"== {e['id']} {e['version']}")
            listed = run(args.cli, package, ["info.commands"], plugins_dir)
            if listed.returncode != 0:
                print(f"error: RustGIS couldn't load it:\n{listed.stderr}")
                failures += 1
                continue
            ids = {c["id"] for c in json.loads(listed.stdout)}

            with zipfile.ZipFile(package) as z:
                root = "" if "plugin.json" in z.namelist() else next(n[: -len("plugin.json")] for n in z.namelist() if n.endswith("/plugin.json"))
                manifest = json.loads(z.read(root + "plugin.json"))
            for c in manifest.get("commands", []):
                cid = f"plugin:{e['id']}/{c['id']}"
                if cid not in ids:
                    print(f"error: {cid} isn't listed")
                    failures += 1
                    continue
                if c.get("params", "{}") not in ("{}", "") and ":" in c.get("params", "") and "?" not in c.get("params", ""):
                    continue
                r = run(args.cli, package, [cid], plugins_dir)
                print(f"   command {c['id']}: {'ok' if r.returncode == 0 else 'failed (allowed): ' + r.stderr.strip()}")
            for t in manifest.get("tools", []):
                params, skip = {}, False
                for p in t.get("params", []):
                    if p["kind"] in SAMPLE:
                        params[p["name"]] = SAMPLE[p["kind"]]
                    elif p["kind"] in FIELDS:
                        params[p["name"]] = FIELDS[p["kind"]]
                    elif p.get("required") and "default" not in p:
                        skip = True
                if skip:
                    print(f"   tool {t['id']}: skipped (a required parameter has no default)")
                    continue
                cmd = "gp.run=" + json.dumps({"tool": f"plugin:{e['id']}/{t['id']}", "params": params})
                r = run(args.cli, package, [cmd], plugins_dir)
                if r.returncode == 0:
                    print(f"   tool {t['id']}: ok")
                else:
                    print(f"error: tool {t['id']} failed: {r.stderr.strip()}")
                    failures += 1
    print(f"{failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
