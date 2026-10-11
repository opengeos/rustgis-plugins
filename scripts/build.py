#!/usr/bin/env python3
"""Builds the RustGIS plugin registry: packages, registry.json, blocklist.json and the catalog.

    python scripts/build.py            # build plugins, package them, write dist/
    python scripts/build.py --check    # also fail on any problem (CI)
    python scripts/build.py --no-cargo # reuse the modules already built
    python scripts/build.py --inspect <checkout> <out> registry/<id>.json...
                                       # unpack the packages these entries point at, for review

Each plugin has an entry in registry/<id>.json:

- a plugin in this repository names its folder: {"id": ..., "path": "plugins/<dir>"}; it is
  built from source with cargo and packaged here.
- a plugin hosted elsewhere names its package: {"id": ..., "source": {"url": "https://...rgplugin",
  "sha256": "..."}}; it is downloaded, checked against the hash, and served from here.

Everything users see (name, version, description, permissions...) comes from the package's
plugin.json, so it can't disagree with what is installed. Output goes to dist/:

    dist/registry.json
    dist/blocklist.json
    dist/packages/<id>/<id>-<version>.rgplugin   (never changes once published)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
TARGET = ROOT / "target" / "wasm32-unknown-unknown" / "release"

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}([-+].*)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PERMISSIONS = {"edit", "files"}
PARAM_KINDS = {"layer", "vectorLayer", "polygonLayer", "pointLayer", "field", "numericField", "number", "text", "choice", "bool"}
CATEGORIES = {
    "Analysis", "Cartography", "Data", "Editing", "Example", "Hydrology", "Imagery", "Raster",
    "Sampling", "Terrain", "Utilities", "Vector", "Visualization",
}
MAX_PACKAGE = 64 * 1024 * 1024
# RustGIS's own tabs: a plugin's tab can't take their names.
RESERVED_TABS = {
    "project", "map", "insert", "analysis", "view", "edit", "imagery", "share", "plugins", "help",
    "feature layer", "labeling", "data", "raster layer", "raster data", "layout",
}
# A fixed timestamp keeps packages byte-for-byte reproducible.
ZIP_TIME = (2026, 1, 1, 0, 0, 0)

problems: list[str] = []


def problem(msg: str) -> None:
    problems.append(msg)
    print(f"error: {msg}", file=sys.stderr)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------------
# WebAssembly checks: the exports RustGIS needs, and no imports it doesn't provide.


def leb(data: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return result, i


def wasm_interface(data: bytes) -> tuple[set[str], list[str]]:
    """Returns (exports, imports as 'module.name') of a WebAssembly module."""
    if data[:4] != b"\0asm":
        raise ValueError("not a WebAssembly module")
    i, exports, imports = 8, set(), []

    def name(i: int) -> tuple[str, int]:
        n, i = leb(data, i)
        return data[i : i + n].decode("utf-8", "replace"), i + n

    while i < len(data):
        sid = data[i]
        size, i = leb(data, i + 1)
        end = i + size
        if sid == 2:  # imports
            count, j = leb(data, i)
            for _ in range(count):
                mod, j = name(j)
                nm, j = name(j)
                kind = data[j]
                j += 1
                imports.append(f"{mod}.{nm}")
                if kind == 0:  # function: type index
                    _, j = leb(data, j)
                elif kind == 1:  # table: reftype + limits
                    j += 1
                    flags, j = leb(data, j)
                    _, j = leb(data, j)
                    if flags & 1:
                        _, j = leb(data, j)
                elif kind == 2:  # memory: limits
                    flags, j = leb(data, j)
                    _, j = leb(data, j)
                    if flags & 1:
                        _, j = leb(data, j)
                elif kind == 3:  # global: type + mutability
                    j += 2
                else:
                    break
        elif sid == 7:  # exports
            count, j = leb(data, i)
            for _ in range(count):
                nm, j = name(j)
                j += 1
                _, j = leb(data, j)
                exports.add(nm)
        i = end
    return exports, imports


# --------------------------------------------------------------------------------------------
# Manifests and packages


def check_manifest(m: dict, where: str) -> None:
    for key in ("id", "name", "version"):
        if not isinstance(m.get(key), str) or not m[key].strip():
            problem(f"{where}: plugin.json needs '{key}'")
    if not ID_RE.match(m.get("id", "")):
        problem(f"{where}: id must be lower-case letters, digits and hyphens")
    if not VERSION_RE.match(m.get("version", "")):
        problem(f"{where}: version must look like 1.0.0")
    if m.get("abi", 1) != 1:
        problem(f"{where}: abi must be 1")
    for p in m.get("permissions", []):
        if p not in PERMISSIONS:
            problem(f"{where}: unknown permission '{p}'")
    if m.get("homepage") and not str(m["homepage"]).startswith("https://"):
        problem(f"{where}: homepage must be an https:// address")
    if not m.get("commands") and not m.get("tools"):
        problem(f"{where}: the plugin adds no commands and no tools")
    for c in m.get("commands", []):
        if not ID_RE.match(c.get("id", "")) or not c.get("label"):
            problem(f"{where}: command {c.get('id')!r} needs a valid id and a label")
    for t in m.get("tools", []):
        if not ID_RE.match(t.get("id", "")) or not t.get("name"):
            problem(f"{where}: tool {t.get('id')!r} needs a valid id and a name")
        for p in t.get("params", []):
            if p.get("kind") not in PARAM_KINDS:
                problem(f"{where}: tool {t.get('id')}: parameter {p.get('name')!r} has unknown kind {p.get('kind')!r}")


def check_tab(m: dict, where: str) -> None:
    """Checks a manifest's own ribbon tab: its label and that every item names a command or tool.

    Args:
        m: The manifest.
        where: Where it came from, for messages.
    """
    tab = m.get("tab")
    if tab is None:
        return
    if not isinstance(tab, dict):
        problem(f"{where}: tab must be an object")
        return
    label = str(tab.get("label", "")).strip()
    if not label or len(label) > 32 or "\n" in label:
        problem(f"{where}: the tab's label must be one line of 1 to 32 characters")
    if label.lower() in RESERVED_TABS:
        problem(f"{where}: the tab can't be called '{label}', the name of one of RustGIS's tabs")
    commands = {c.get("id") for c in m.get("commands", [])}
    tools = {t.get("id") for t in m.get("tools", [])}

    def entry(e: object) -> None:
        ok = isinstance(e, str) and (e[5:] in tools if e.startswith("tool:") else e in commands)
        if not ok:
            problem(f"{where}: the tab names {e!r}, which isn't one of the plugin's commands or tools (tool:<id>)")

    groups = tab.get("groups") or []
    if not 1 <= len(groups) <= 8:
        problem(f"{where}: the tab needs 1 to 8 groups")
    for g in groups:
        items = g.get("items") or []
        if not g.get("label") or not 1 <= len(items) <= 16:
            problem(f"{where}: tab group {g.get('label')!r} needs a label and 1 to 16 items")
        for item in items:
            if isinstance(item, dict):
                if not item.get("menu") or not 1 <= len(item.get("items") or []) <= 32:
                    problem(f"{where}: menu {item.get('menu')!r} needs a label and 1 to 32 items")
                for e in item.get("items") or []:
                    entry(e)
            else:
                entry(item)


def check_module(wasm: bytes, where: str) -> None:
    try:
        exports, imports = wasm_interface(wasm)
    except (ValueError, IndexError) as e:
        problem(f"{where}: unreadable module: {e}")
        return
    for e in ("memory", "rustgis_alloc", "rustgis_call"):
        if e not in exports:
            problem(f"{where}: the module doesn't export '{e}' (build it with the rustgis-plugin SDK)")
    for imp in imports:
        if imp not in ("rustgis.call", "rustgis.result", "rustgis.log"):
            problem(f"{where}: the module imports '{imp}', which RustGIS doesn't provide")


def package(manifest_path: Path, wasm: bytes) -> bytes:
    """A reproducible .rgplugin: plugin.json, the module and the README, in a fixed order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        files = [("plugin.json", manifest_path.read_bytes())]
        manifest = json.loads(files[0][1])
        files.append((manifest.get("entry", "plugin.wasm"), wasm))
        readme = manifest_path.parent / "README.md"
        if readme.is_file():
            files.append(("README.md", readme.read_bytes()))
        for name, data in files:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


def read_package(data: bytes, where: str) -> tuple[dict, bytes] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            root = "" if "plugin.json" in names else next((n[: -len("plugin.json")] for n in names if n.endswith("/plugin.json")), None)
            if root is None:
                problem(f"{where}: the package has no plugin.json")
                return None
            manifest = json.loads(z.read(root + "plugin.json"))
            wasm = z.read(root + manifest.get("entry", "plugin.wasm"))
            return manifest, wasm
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
        problem(f"{where}: unreadable package: {e}")
        return None


def crate_name(folder: Path) -> str:
    return tomllib.loads((folder / "Cargo.toml").read_text())["package"]["name"].replace("-", "_")


def download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("source urls must be https://")
    req = urllib.request.Request(url, headers={"User-Agent": "rustgis-plugins-registry"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(MAX_PACKAGE + 1)
    if len(data) > MAX_PACKAGE:
        raise ValueError("the package is larger than 64 MB")
    return data


# --------------------------------------------------------------------------------------------


def inspect(checkout: Path, out: Path, files: list[str]) -> int:
    """For code review: downloads the package each changed registry entry with a `source` points
    at, checks its SHA-256, and writes what it holds to <out>/plugins/<id>/ (plugin.json, README,
    module interface) and a summary to <out>/report.md. Packages are read as data, never run."""
    out.mkdir(parents=True, exist_ok=True)
    rows, notes = [], []
    for f in files:
        try:
            reg = json.loads((checkout / f).read_text())
        except (OSError, json.JSONDecodeError) as e:
            notes.append(f"- `{f}`: unreadable ({e})")
            continue
        src = reg.get("source")
        if not isinstance(src, dict):
            continue
        pid = str(reg.get("id", ""))
        if not ID_RE.match(pid):
            notes.append(f"- `{f}`: invalid id, not inspected")
            continue
        try:
            data = download(str(src.get("url", "")))
        except Exception as e:  # noqa: BLE001 - reported as a finding
            notes.append(f"- `{pid}`: could not be downloaded ({e}); not inspected")
            continue
        got = sha256(data)
        if got != src.get("sha256"):
            notes.append(f"- `{pid}`: SHA-256 mismatch (registry {src.get('sha256')}, download {got}); not inspected")
            continue
        read = read_package(data, pid)
        if read is None:
            notes.append(f"- `{pid}`: not a readable package; not inspected")
            continue
        manifest, wasm = read
        dest = out / "plugins" / pid
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "plugin.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            readme = next((n for n in names if n.rsplit("/", 1)[-1].lower() == "readme.md"), None)
            if readme:
                (dest / "README.md").write_bytes(z.read(readme)[:512 * 1024])
        try:
            exports, imports = wasm_interface(wasm)
        except (ValueError, IndexError) as e:
            exports, imports = set(), [f"unreadable: {e}"]
        (dest / "module.txt").write_text(
            "exports: " + ", ".join(sorted(exports)) + "\nimports: " + ", ".join(imports) + "\nfiles: " + ", ".join(names) + "\n"
        )
        rows.append(
            f"| `{pid}` | {manifest.get('version')} | {len(data):,} B | {len(wasm):,} B | {', '.join(manifest.get('permissions', [])) or 'none'} | {', '.join(imports) or 'none'} |"
        )
    report = ["# Packages this PR points at", ""]
    if rows:
        report += ["| id | version | package | module | permissions | module imports |", "|---|---|---|---|---|---|", *rows, ""]
    report += notes or ["All changed entries with a source were inspected."]
    (out / "report.md").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--inspect":
        if len(sys.argv) < 4:
            print("usage: build.py --inspect <checkout> <out> registry/<id>.json...", file=sys.stderr)
            return 2
        return inspect(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit with an error on any problem")
    ap.add_argument("--no-cargo", action="store_true", help="don't run cargo; use the modules already built")
    args = ap.parse_args()

    if not args.no_cargo:
        subprocess.run(["cargo", "build", "--release", "--target", "wasm32-unknown-unknown", "--workspace", "--exclude", "rustgis-plugin"], cwd=ROOT, check=True)

    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "packages").mkdir(parents=True)

    entries = []
    for path in sorted((ROOT / "registry").glob("*.json")):
        where = f"registry/{path.name}"
        try:
            reg = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            problem(f"{where}: {e}")
            continue
        if reg.get("id") != path.stem:
            problem(f"{where}: the file name must be the plugin id")
            continue
        for c in reg.get("categories", []):
            if c not in CATEGORIES:
                problem(f"{where}: unknown category '{c}' (one of {', '.join(sorted(CATEGORIES))})")
        if "path" in reg:
            folder = ROOT / reg["path"]
            manifest_path = folder / "plugin.json"
            if not manifest_path.is_file():
                problem(f"{where}: {reg['path']}/plugin.json is missing")
                continue
            wasm_path = TARGET / f"{crate_name(folder)}.wasm"
            if not wasm_path.is_file():
                problem(f"{where}: {wasm_path.relative_to(ROOT)} wasn't built")
                continue
            data = package(manifest_path, wasm_path.read_bytes())
        elif "source" in reg:
            src = reg["source"]
            if not SHA_RE.match(src.get("sha256", "")):
                problem(f"{where}: source.sha256 must be the package's lower-case hex SHA-256")
                continue
            try:
                data = download(src.get("url", ""))
            except Exception as e:  # noqa: BLE001 - any download failure is a registry problem
                problem(f"{where}: can't download {src.get('url')}: {e}")
                continue
            if sha256(data) != src["sha256"]:
                problem(f"{where}: {src['url']} doesn't match source.sha256 (it is {sha256(data)})")
                continue
        else:
            problem(f"{where}: needs a 'path' (a plugin in this repository) or a 'source' (a package hosted elsewhere)")
            continue
        read = read_package(data, where)
        if read is None:
            continue
        manifest, wasm = read
        check_manifest(manifest, where)
        check_tab(manifest, where)
        check_module(wasm, where)
        if manifest.get("id") != reg["id"]:
            problem(f"{where}: the package's id is '{manifest.get('id')}'")
            continue
        name = f"{manifest['id']}-{manifest['version']}.rgplugin"
        out = DIST / "packages" / manifest["id"] / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        entries.append(
            {
                "id": manifest["id"],
                "name": manifest["name"],
                "version": manifest["version"],
                "description": manifest.get("description", ""),
                "author": manifest.get("author", ""),
                "homepage": manifest.get("homepage", ""),
                "license": manifest.get("license", ""),
                "categories": reg.get("categories", []),
                "package": f"packages/{manifest['id']}/{name}",
                "sha256": sha256(data),
                "size": len(data),
                "minRustgisVersion": manifest.get("minRustgisVersion", ""),
                "permissions": manifest.get("permissions", []),
                "commands": [c.get("label", "").replace("\n", " ") for c in manifest.get("commands", [])],
                "tools": [t.get("name", "") for t in manifest.get("tools", [])],
                "tab": (manifest.get("tab") or {}).get("label", ""),
            }
        )

    blocklist = json.loads((ROOT / "blocklist.json").read_text())
    for b in blocklist.get("blocked", []):
        if not ID_RE.match(b.get("id", "")) or ("sha256" in b and not SHA_RE.match(b["sha256"])):
            problem(f"blocklist.json: bad entry {b}")
        listed = next((e for e in entries if e["id"] == b.get("id")), None)
        if listed and ("sha256" not in b or b["sha256"] == listed["sha256"]):
            problem(f"blocklist.json: {b['id']} is blocked but still listed in registry/")

    entries.sort(key=lambda e: e["name"].lower())
    (DIST / "registry.json").write_text(json.dumps({"version": 1, "plugins": entries}, indent=2, ensure_ascii=False) + "\n")
    (DIST / "blocklist.json").write_text(json.dumps(blocklist, indent=2) + "\n")
    print(f"{len(entries)} plugins -> {DIST.relative_to(ROOT)}/registry.json")
    if args.check and problems:
        print(f"{len(problems)} problems", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
