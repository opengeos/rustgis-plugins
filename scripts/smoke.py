#!/usr/bin/env python3
"""Loads packages into RustGIS and runs them on the World Atlas sample.

    python scripts/smoke.py [--cli path/to/rustgis-cli]         # the packages this build made
    python scripts/smoke.py --all [--cli path/to/rustgis-cli]   # every listed package (nightly)
    python scripts/smoke.py --runtime wasm|process ...           # only WebAssembly or process plugins

For each plugin: RustGIS must load it and list its commands; each tool runs with its defaults
(layer parameters get a sample layer of the right kind) and must succeed; each command that
needs no parameters runs too (a failure there is reported but doesn't fail the check, since a
command may need a selection or a layer the sample doesn't have).

A process plugin ("runtime": "process") runs from its unpacked files, so it is installed into a
scratch plugins folder first (`rustgis-cli plugins install`, which also makes its Python
environment with uv: this downloads Python and its packages), run as installed, and uninstalled.
One with no program for Linux is skipped (RustGIS can't load it here; build.py checks its files).
"""

from __future__ import annotations

import argparse
import hashlib
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
MAX_PACKAGE = 64 * 1024 * 1024
# A sample layer for each layer kind, and a field of it.
SAMPLE = {"polygonLayer": "Countries", "pointLayer": "Cities", "vectorLayer": "Countries", "layer": "Countries"}
FIELDS = {"field": "name", "numericField": "pop_est"}


# Installing a process plugin can download Python and its packages.
INSTALL_TIMEOUT = 1800


def run(cli: str, package: Path, cmds: list[str], plugins_dir: str, installed: bool = False) -> subprocess.CompletedProcess:
    """Runs commands in rustgis-cli on the sample, with the plugin loaded.

    Args:
        cli: The rustgis-cli program.
        package: The plugin's package.
        cmds: The commands to run, in order.
        plugins_dir: The scratch plugins folder.
        installed: The plugin is installed in `plugins_dir` (a process plugin): load the
            installed plugins rather than the package.

    Returns:
        The finished process.
    """
    args = [cli, "run", "--sample"] if installed else [cli, "run", "--no-plugins", "--plugin", str(package), "--sample"]
    for c in cmds:
        args += ["--cmd", c]
    env = dict(os.environ, RUSTGIS_PLUGINS_DIR=plugins_dir)
    return subprocess.run(args, capture_output=True, text=True, timeout=600, env=env)


def plugins(cli: str, args: list[str], plugins_dir: str) -> subprocess.CompletedProcess:
    """Runs `rustgis-cli plugins <args>` on the scratch plugins folder.

    Args:
        cli: The rustgis-cli program.
        args: The subcommand and its argument (`install <file>`, `uninstall <id>`).
        plugins_dir: The scratch plugins folder.

    Returns:
        The finished process.
    """
    env = dict(os.environ, RUSTGIS_PLUGINS_DIR=plugins_dir)
    try:
        return subprocess.run([cli, "plugins", *args], capture_output=True, text=True, timeout=INSTALL_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", f"it took more than {INSTALL_TIMEOUT // 60} minutes")


def runs_on_linux(manifest: dict) -> bool:
    """Whether a process plugin's `run` names a program for Linux (its own, or the default).

    Args:
        manifest: The plugin's plugin.json.

    Returns:
        True when it does.
    """
    run = manifest.get("run")
    return isinstance(run, list) or (isinstance(run, dict) and bool(run.get("linux") or run.get("default")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=os.environ.get("RUSTGIS_CLI") or shutil.which("rustgis-cli") or "rustgis-cli")
    ap.add_argument("--all", action="store_true", help="every listed package, downloading published ones")
    ap.add_argument("--shard", default="1/1", help="with --all, run part k of n (k/n), for parallel jobs")
    ap.add_argument("--runtime", choices=("all", "wasm", "process"), default="all", help="run only WebAssembly or process plugins")
    args = ap.parse_args()
    k, n = (int(x) for x in args.shard.split("/"))
    registry = json.loads((DIST / "registry.json").read_text())
    failures = 0
    with tempfile.TemporaryDirectory() as plugins_dir, tempfile.TemporaryDirectory() as downloads:
        ran = 0
        for i, e in enumerate(registry["plugins"]):
            if i % n != k - 1:
                continue
            # Built by this run, or (with --all) downloaded from the bucket.
            file_name = e["package"].rsplit("/", 1)[-1]
            package = DIST / "packages" / e["id"] / file_name
            if not package.is_file():
                if not args.all:
                    continue
                package = Path(downloads) / file_name
                if not str(e["package"]).startswith("https://"):
                    print(f"error: {e['id']}: package URL isn't https: {e['package']}")
                    failures += 1
                    continue
                try:
                    req = urllib.request.Request(e["package"], headers={"User-Agent": "rustgis-plugins-smoke"})
                    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - https only, checked above
                        data = r.read(MAX_PACKAGE + 1)
                except Exception as err:  # noqa: BLE001 - one bad download mustn't stop the run
                    print(f"error: {e['id']}: can't download {e['package']}: {err}")
                    failures += 1
                    continue
                # Test what users install: the bytes the registry lists.
                if len(data) > MAX_PACKAGE or hashlib.sha256(data).hexdigest() != e.get("sha256"):
                    print(f"error: {e['id']}: {e['package']} doesn't match the registry's SHA-256")
                    failures += 1
                    continue
                package.write_bytes(data)
            with zipfile.ZipFile(package) as z:
                root = "" if "plugin.json" in z.namelist() else next(n[: -len("plugin.json")] for n in z.namelist() if n.endswith("/plugin.json"))
                manifest = json.loads(z.read(root + "plugin.json"))
            process = manifest.get("runtime") == "process"
            if args.runtime != "all" and process != (args.runtime == "process"):
                print(f"== {e['id']} {e['version']}: a {'process' if process else 'WebAssembly'} plugin, not run here")
                continue
            print(f"== {e['id']} {e['version']}")
            if process and not runs_on_linux(manifest):
                # RustGIS won't load it here at all; build.py checked its manifest and files.
                print("   skipped: a process plugin with no program for Linux, which RustGIS can't run here (run it on its platforms by hand)")
                continue
            ran += 1
            try:
                # A process plugin runs from its unpacked files: installed (with its Python
                # environment) here, and removed after, even when the install half worked.
                if process and (got := plugins(args.cli, ["install", str(package)], plugins_dir)).returncode != 0:
                    print(f"error: RustGIS couldn't install it:\n{got.stderr}")
                    failures += 1
                    continue
                failures += exercise(args.cli, package, e["id"], manifest, plugins_dir, process)
            finally:
                if process:
                    plugins(args.cli, ["uninstall", e["id"]], plugins_dir)
    if ran == 0:
        print("no new packages to run" if not args.all else "no packages in this shard")
    print(f"{ran} plugins run, {failures} failures")
    return 1 if failures else 0


def exercise(cli: str, package: Path, pid: str, manifest: dict, plugins_dir: str, installed: bool) -> int:
    """Loads a plugin in rustgis-cli and runs its commands and tools on the sample.

    Args:
        cli: The rustgis-cli program.
        package: The plugin's package.
        pid: The plugin's id.
        manifest: Its plugin.json.
        plugins_dir: The scratch plugins folder.
        installed: It is installed there (a process plugin) rather than loaded from the package.

    Returns:
        The number of failures.
    """
    failures = 0
    listed = run(cli, package, ["info.commands"], plugins_dir, installed)
    if listed.returncode != 0:
        print(f"error: RustGIS couldn't load it:\n{listed.stderr}")
        return 1
    ids = {c["id"] for c in json.loads(listed.stdout)}
    for c in manifest.get("commands", []):
        cid = f"plugin:{pid}/{c['id']}"
        if cid not in ids:
            print(f"error: {cid} isn't listed")
            failures += 1
            continue
        if c.get("params", "{}") not in ("{}", "") and ":" in c.get("params", "") and "?" not in c.get("params", ""):
            continue
        r = run(cli, package, [cid], plugins_dir, installed)
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
        cmd = "gp.run=" + json.dumps({"tool": f"plugin:{pid}/{t['id']}", "params": params})
        r = run(cli, package, [cmd], plugins_dir, installed)
        if r.returncode == 0:
            print(f"   tool {t['id']}: ok")
        else:
            print(f"error: tool {t['id']} failed: {r.stderr.strip()}")
            failures += 1
    return failures


if __name__ == "__main__":
    sys.exit(main())
