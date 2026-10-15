#!/usr/bin/env python3
"""Builds the RustGIS plugin registry: registry.json, the package index and new packages.

    python scripts/build.py            # build what changed, write dist/
    python scripts/build.py --check    # also fail on any problem (CI)
    python scripts/build.py --no-cache # ignore the published index (rebuild or reuse from R2)
    python scripts/build.py --inspect <checkout> <out> registry/<id>.json...
                                       # unpack the packages these entries point at, for review

Each plugin has an entry in registry/<id>.json:

- a plugin in this repository names its folder: {"id": ..., "path": "plugins/<dir>"}; it is
  built from source with cargo and packaged here.
- a plugin hosted elsewhere names its package: {"id": ..., "source": {"url": "https://...rgplugin",
  "sha256": "..."}}; it is downloaded and checked against the hash.

Packages are served from packages.rustgis.app (a Cloudflare R2 bucket) at <id>/<id>-<version>.rgplugin
and never change once published. So the build is incremental: the previous deploy's package
index (plugins.rustgis.app/packages.json) says what is published, and only plugins whose version
(or source) changed are built or downloaded. An in-repository plugin whose files changed without
a version bump is a problem. Without the index, packages already in the bucket are reused rather
than rebuilt. Output goes to dist/:

    dist/registry.json      what RustGIS lists (every plugin, with its package URL and SHA-256)
    dist/packages.json      the package index: each plugin's package, manifest and source hash
    dist/packages/<id>/...  the packages built or downloaded by this run (to upload)
    dist/new.txt            their paths, relative to dist/packages

Everything users see (name, version, description, permissions...) comes from the package's
plugin.json, so it can't disagree with what is installed.
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
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Where packages are served (the R2 bucket's domain), and the previous deploy's package index.
PACKAGES_URL = "https://packages.rustgis.app"
INDEX_URL = "https://plugins.rustgis.app/packages.json"
DIST = ROOT / "dist"
TARGET = ROOT / "target" / "wasm32-unknown-unknown" / "release"

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# Versions name objects in the bucket, so only safe characters: 1.2.3, 1.2.3-beta.1, 1.2.3+build.5.
VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}([-+][0-9A-Za-z]+(\.[0-9A-Za-z]+)*)*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PERMISSIONS = {"edit", "files", "network"}
PARAM_KINDS = {"layer", "vectorLayer", "polygonLayer", "pointLayer", "field", "numericField", "number", "text", "choice", "bool", "file"}
EXTENSION_RE = re.compile(r"[A-Za-z0-9]{1,16}")
CATEGORIES = {
    "Analysis", "Basemaps", "Cartography", "Data", "Editing", "Example", "Hydrology", "Imagery",
    "Raster", "Sampling", "Terrain", "Utilities", "Vector", "Visualization",
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


def listing_i18n(manifest: dict) -> dict:
    """The translations of a plugin's name and description, for its registry entry.

    The Plugin Manager lists plugins in the interface language before they're installed, so
    the entry carries these two strings from the manifest's `i18n`. Keep it in step with
    `preview.py`'s copy: CI checks the two scripts build the same entry.

    Args:
        manifest: The package's plugin.json.

    Returns:
        `{lang: {english: translation}}` for the languages that translate either string.
    """
    tables = manifest.get("i18n")
    out: dict = {}
    for lang, table in sorted(tables.items()) if isinstance(tables, dict) else []:
        if not isinstance(table, dict):
            continue
        keep = {k: v for k in (manifest.get("name"), manifest.get("description")) if isinstance(k, str) and isinstance(v := table.get(k), str) and v.strip()}
        if keep:
            out[lang] = keep
    return out


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
            # RustGIS hands a tool a file only with `files`, and filters its picker by plain extensions.
            if p.get("kind") == "file" and "files" not in m.get("permissions", []):
                problem(f"{where}: tool {t.get('id')}: file parameter {p.get('name')!r} needs the 'files' permission")
            exts = p.get("extensions", [])
            if not isinstance(exts, list) or len(exts) > 16 or not all(isinstance(e, str) and EXTENSION_RE.fullmatch(e) for e in exts):
                problem(f"{where}: tool {t.get('id')}: parameter {p.get('name')!r} has invalid extensions (letters and digits, no dot)")


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


def package_name(folder: Path) -> str:
    """The cargo package name of an in-repository plugin (for `cargo build -p`)."""
    return tomllib.loads((folder / "Cargo.toml").read_text())["package"]["name"]


def crate_name(folder: Path) -> str:
    """The plugin's crate name, which names its .wasm file."""
    return package_name(folder).replace("-", "_")


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


def tree_hash(folder: Path) -> str:
    """Hashes an in-repository plugin's files (its source, manifest and README), so a change
    without a version bump can be caught. Only the plugin's own folder counts: a change to the
    shared SDK, the workspace lockfile or the toolchain doesn't republish existing versions.

    Args:
        folder: The plugin's folder.

    Returns:
        The lower-case hex SHA-256 over every file's relative path and contents.
    """
    h = hashlib.sha256()
    # The files git tracks (no build output, editor backups or links to elsewhere); every file
    # when this isn't a git checkout.
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", "."], cwd=folder, capture_output=True, check=True).stdout
        files = sorted(folder / f for f in out.decode().split("\0") if f)
    except (OSError, subprocess.CalledProcessError):
        files = sorted(p for p in folder.rglob("*") if p.is_file() and p.relative_to(folder).parts[0] != "target")
    for f in files:
        if not f.is_file() or f.is_symlink():
            continue
        h.update(f.relative_to(folder).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


MAX_INDEX = 256 * 1024 * 1024


def load_index(source: str | None) -> dict:
    """Reads the previous deploy's package index.

    Only a missing index (404: the first deploy) means "no index". Any other failure (network,
    timeout, a truncated or malformed file) is a problem, because without the index the check
    that in-repository plugins bumped their version can't run.

    Args:
        source: A URL or a file path, or None for no index.

    Returns:
        The index's plugins by id (empty when there is none or it can't be read).
    """
    if not source:
        return {}
    try:
        if source.startswith("https://"):
            req = urllib.request.Request(f"{source}?{time.time_ns()}", headers={"User-Agent": "rustgis-plugins-registry", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read(MAX_INDEX + 1)
        else:
            raw = Path(source).read_bytes()
        if len(raw) > MAX_INDEX:
            raise ValueError(f"larger than {MAX_INDEX // (1024 * 1024)} MB")
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("note: no package index yet (first deploy); reusing packages already in the bucket", file=sys.stderr)
            return {}
        problem(f"the package index {source} couldn't be read ({e}); rerun once it is reachable")
        return {}
    except Exception as e:  # noqa: BLE001 - any other failure stops a checked build
        problem(f"the package index {source} couldn't be read ({e}); rerun once it is reachable")
        return {}
    return {p["id"]: p for p in data.get("plugins", []) if valid_cached(p)}


def valid_cached(p: object) -> bool:
    """Whether a package index entry has what reusing it needs; anything else is rebuilt.

    Args:
        p: An entry from the index.

    Returns:
        True when it can be reused.
    """
    if not isinstance(p, dict):
        return False
    m = p.get("manifest")
    return (
        isinstance(p.get("id"), str)
        and isinstance(p.get("version"), str)
        and SHA_RE.match(str(p.get("sha256", ""))) is not None
        and isinstance(p.get("size"), int)
        and isinstance(p.get("origin"), dict)
        and isinstance(m, dict)
        and m.get("id") == p["id"]
        and m.get("version") == p["version"]
    )


def published(url: str) -> bytes | None:
    """Downloads a package already in the bucket (bypassing caches, so a cached 404 isn't
    believed).

    Args:
        url: The package's URL.

    Returns:
        Its bytes, or None when it isn't there.

    Raises:
        Exception: when the bucket can't be asked (anything but a 404).
    """
    try:
        return download(f"{url}?{time.time_ns()}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


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
    ap.add_argument("--index", default=INDEX_URL, help="the previous package index (URL or file)")
    ap.add_argument("--no-cache", action="store_true", help="ignore the package index")
    ap.add_argument("--no-bucket", action="store_true", help="don't look for packages in the bucket (offline)")
    args = ap.parse_args()

    index = {} if args.no_cache else load_index(args.index)
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "packages").mkdir(parents=True)

    # What each entry needs: reuse the published package, build it, or download it.
    plans = []
    for path in sorted((ROOT / "registry").glob("*.json")):
        where = f"registry/{path.name}"
        try:
            reg = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            problem(f"{where}: {e}")
            continue
        if reg.get("id") != path.stem or not ID_RE.match(path.stem):
            problem(f"{where}: the file name must be the plugin id")
            continue
        for c in reg.get("categories", []):
            if c not in CATEGORIES:
                problem(f"{where}: unknown category '{c}' (one of {', '.join(sorted(CATEGORIES))})")
        cached = index.get(reg["id"])
        if "path" in reg:
            folder = ROOT / reg["path"]
            manifest_path = folder / "plugin.json"
            if not manifest_path.is_file():
                problem(f"{where}: {reg['path']}/plugin.json is missing")
                continue
            try:
                version = str(json.loads(manifest_path.read_text()).get("version", ""))
            except (OSError, json.JSONDecodeError) as e:
                problem(f"{where}: {reg['path']}/plugin.json: {e}")
                continue
            tree = tree_hash(folder)
            origin = {"path": reg["path"], "tree": tree}
            if cached and cached.get("version") == version and cached.get("origin", {}).get("path"):
                if cached["origin"].get("tree") != tree:
                    problem(f"{where}: {reg['path']} changed but its version is still {version}: published packages never change, so bump the version")
                    continue
                plans.append((reg, where, "reuse", cached, origin))
            else:
                plans.append((reg, where, "build", folder, origin))
        elif "source" in reg:
            src = reg["source"]
            if not SHA_RE.match(src.get("sha256", "")):
                problem(f"{where}: source.sha256 must be the package's lower-case hex SHA-256")
                continue
            origin = {"source": src.get("url", ""), "sha256": src["sha256"]}
            if cached and cached.get("origin", {}).get("sha256") == src["sha256"]:
                plans.append((reg, where, "reuse", cached, origin))
            else:
                plans.append((reg, where, "download", src, origin))
        else:
            problem(f"{where}: needs a 'path' (a plugin in this repository) or a 'source' (a package hosted elsewhere)")

    # Build only the plugins that need it.
    to_build = [package_name(p[3]) for p in plans if p[2] == "build"]
    if to_build and not args.no_cargo:
        cmd = ["cargo", "build", "--release", "--target", "wasm32-unknown-unknown"]
        for c in to_build:
            cmd += ["-p", c]
        subprocess.run(cmd, cwd=ROOT, check=True)

    entries, packages, new = [], [], []
    for reg, where, how, what, origin in plans:
        if how == "reuse":
            manifest, digest, size, fresh = what["manifest"], what["sha256"], what["size"], None
            # Rules added since it was published apply to it too.
            check_manifest(manifest, where)
            check_tab(manifest, where)
        else:
            if how == "build":
                wasm_path = TARGET / f"{crate_name(what)}.wasm"
                if not wasm_path.is_file():
                    problem(f"{where}: {wasm_path.relative_to(ROOT)} wasn't built")
                    continue
                data = package(what / "plugin.json", wasm_path.read_bytes())
            else:
                try:
                    data = download(what.get("url", ""))
                except Exception as e:  # noqa: BLE001 - any download failure is a registry problem
                    problem(f"{where}: can't download {what.get('url')}: {e}")
                    continue
                if sha256(data) != what["sha256"]:
                    problem(f"{where}: {what['url']} doesn't match source.sha256 (it is {sha256(data)})")
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
            # A version already in the bucket is never replaced: reuse it (a rebuild needn't be
            # byte-identical), unless it's a different plugin under that name.
            url = f"{PACKAGES_URL}/{manifest['id']}/{manifest['id']}-{manifest['version']}.rgplugin"
            try:
                existing = None if args.no_bucket else published(url)
            except Exception as e:  # noqa: BLE001 - reported like the other downloads
                problem(f"{where}: can't check {url}: {e}")
                continue
            if existing is not None and how == "download" and sha256(existing) != sha256(data):
                problem(f"{where}: {manifest['id']} {manifest['version']} is already published with different contents; bump the version")
                continue
            if existing is not None and how == "build":
                # A rebuild needn't be byte-identical, but what it declares must match what is
                # published under this version: otherwise the version wasn't bumped.
                old = read_package(existing, f"{url} (published)")
                if old is None or old[0] != manifest:
                    problem(f"{where}: plugin.json differs from the published {manifest['id']} {manifest['version']}; bump the version")
                    continue
                if old[1] != wasm:
                    print(
                        f"warning: {where}: the rebuilt module differs from the published {manifest['id']} {manifest['version']}"
                        " (a different toolchain, or code changed without a version bump); the published one is kept",
                        file=sys.stderr,
                    )
            data = existing if existing is not None else data
            digest, size, fresh = sha256(data), len(data), data if existing is None else None
        name = f"{manifest['id']}-{manifest['version']}.rgplugin"
        if fresh is not None:
            out = DIST / "packages" / manifest["id"] / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(fresh)
            new.append(f"{manifest['id']}/{name}")
        packages.append({"id": manifest["id"], "version": manifest["version"], "sha256": digest, "size": size, "origin": origin, "manifest": manifest})
        # scripts/preview.py's entry_for() makes the same entry from a manifest (CI checks they agree).
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
                "package": f"{PACKAGES_URL}/{manifest['id']}/{name}",
                "sha256": digest,
                "size": size,
                "minRustgisVersion": manifest.get("minRustgisVersion", ""),
                "permissions": manifest.get("permissions", []),
                "commands": [c.get("label", "").replace("\n", " ") for c in manifest.get("commands", [])],
                "tools": [t.get("name", "") for t in manifest.get("tools", [])],
                "tab": (manifest.get("tab") or {}).get("label", ""),
                "i18n": listing_i18n(manifest),
            }
        )
    print(f"{len(new)} new packages, {len(entries) - len(new)} already published")

    blocklist = json.loads((ROOT / "blocklist.json").read_text())
    for b in blocklist.get("blocked", []):
        if not ID_RE.match(b.get("id", "")) or ("sha256" in b and not SHA_RE.match(b["sha256"])):
            problem(f"blocklist.json: bad entry {b}")
        listed = next((e for e in entries if e["id"] == b.get("id")), None)
        if listed and ("sha256" not in b or b["sha256"] == listed["sha256"]):
            problem(f"blocklist.json: {b['id']} is blocked but still listed in registry/")

    entries.sort(key=lambda e: e["name"].lower())
    (DIST / "registry.json").write_text(json.dumps({"version": 1, "plugins": entries}, ensure_ascii=False, separators=(",", ":")) + "\n")
    (DIST / "blocklist.json").write_text(json.dumps(blocklist, indent=2) + "\n")
    packages.sort(key=lambda p: p["id"])
    (DIST / "packages.json").write_text(json.dumps({"version": 1, "plugins": packages}, ensure_ascii=False) + "\n")
    (DIST / "new.txt").write_text("".join(f"{n}\n" for n in new))
    print(f"{len(entries)} plugins -> {DIST.relative_to(ROOT)}/registry.json")
    if args.check and problems:
        print(f"{len(problems)} problems", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
