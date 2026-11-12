#!/usr/bin/env python3
"""Turns a registry build into a pull request preview that RustGIS can install from.

    python scripts/preview.py dist preview                        # in CI, after build.py
    python scripts/preview.py --site preview <pr> <sha> <out> [--published registry.json]

The first form makes the preview's data from `dist/` (what `build.py` wrote): `registry.json`
lists every plugin, as the real registry does, except that the packages this pull request built
(`dist/new.txt`) are served from the preview itself, by a path relative to the registry. Those
packages are copied beside it.

The second form is run by the deploy job on the base branch's code. It copies only the
registry and the packages out of the (untrusted) preview data, checking each package against its
entry and its own plugin.json, lists every other plugin from the published registry, and writes
the landing page and headers itself, so a pull request can't publish pages of its own.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import html
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

PACKAGE_RE = re.compile(r"([a-z][a-z0-9-]{0,63})/\1-[0-9A-Za-z.+-]{1,64}\.rgplugin")
# Cloudflare Pages takes files of at most 25 MiB; the whole preview is kept small too.
MAX_PACKAGE = 25 * 1024 * 1024
MAX_SITE = 200 * 1024 * 1024
MAX_REGISTRY = 32 * 1024 * 1024
WEB_APP = "https://web.rustgis.app/"
PUBLISHED = "https://packages.rustgis.app/"
# The platforms an entry can name a package for (build.py's PACKAGE_PLATFORMS).
PLATFORMS = ("windows", "macos", "linux")


def make(dist: Path, out: Path) -> int:
    """Writes the preview's data (registry and packages) from a registry build.

    Args:
        dist: The folder `build.py` wrote.
        out: The folder to write the preview to (replaced).

    Returns:
        The process exit code.
    """
    registry = json.loads((dist / "registry.json").read_text())
    new = [n.strip() for n in (dist / "new.txt").read_text().splitlines() if n.strip()]
    if out.exists():
        shutil.rmtree(out)
    (out / "packages").mkdir(parents=True)
    previewed = []
    for name in new:
        if not PACKAGE_RE.fullmatch(name):
            print(f"preview: skipping unexpected package name {name!r}", file=sys.stderr)
            continue
        dest = out / "packages" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist / "packages" / name, dest)
        pid = name.split("/", 1)[0]
        for e in registry.get("plugins", []):
            # The plugin's only package, or one of its packages by platform.
            for ref in [e, *e.get("packages", {}).values()]:
                if e.get("id") == pid and ref.get("package", "").endswith("/" + name):
                    # Relative to registry.json, which RustGIS resolves against the registry's address.
                    ref["package"] = f"packages/{name}"
                    if not any(p["id"] == pid for p in previewed):
                        previewed.append({"id": pid, "name": e.get("name", pid), "version": e.get("version", "")})
    (out / "registry.json").write_text(json.dumps(registry, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"preview: {len(previewed)} plugin(s) from this pull request: {', '.join(p['id'] for p in previewed) or 'none'}")
    return 0


def listing_i18n(manifest: dict) -> dict:
    """The translations of a plugin's name and description, for its registry entry.

    The Plugin Manager lists plugins in the interface language before they're installed, so
    the entry carries these two strings from the manifest's `i18n`. Keep it in step with
    `build.py`'s copy: CI checks the two scripts build the same entry.

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


def runtime_fields(manifest: dict) -> dict:
    """What a registry entry says about how the plugin runs: nothing for a WebAssembly plugin.

    A process plugin's entry says `"runtime": "process"`, and `"python": "<version>"` when
    RustGIS makes it a Python environment. Keep it in step with `build.py`'s copy: CI checks the
    two scripts build the same entry.

    Args:
        manifest: The package's plugin.json.

    Returns:
        The fields to add to the entry.
    """
    if manifest.get("runtime") != "process":
        return {}
    out: dict = {"runtime": "process"}
    py = manifest.get("python")
    if isinstance(py, dict):
        out["python"] = str(py.get("version", "3.12"))
    return out


def entry_for(manifest: dict, e: dict, pkg: str) -> dict:
    """The registry entry build.py writes for a package with this manifest.

    Keep it in step with the entry `build.py` builds: CI runs `--site` on its own build, so a
    field added there and not here fails the pull request that adds it.

    Args:
        manifest: The package's plugin.json.
        e: The entry being checked (its categories, hash and size are taken from it; the hash
            and size are checked against the package separately).
        pkg: The package's address in the registry, or None for an entry with a package per
            platform (its `packages` are then taken from it, and checked separately too).

    Returns:
        The entry.
    """
    where = {"packages": e.get("packages")} if pkg is None else {"package": pkg, "sha256": e.get("sha256"), "size": e.get("size")}
    return {
        "id": manifest.get("id"),
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "homepage": manifest.get("homepage", ""),
        "license": manifest.get("license", ""),
        "categories": e.get("categories", []),
        **where,
        "minRustgisVersion": manifest.get("minRustgisVersion", ""),
        "permissions": manifest.get("permissions", []),
        "commands": [c.get("label", "").replace("\n", " ") for c in manifest.get("commands", []) if isinstance(c, dict)],
        "tools": [t.get("name", "") for t in manifest.get("tools", []) if isinstance(t, dict)],
        "tab": tab.get("label", "") if isinstance(tab := manifest.get("tab") or {}, dict) else "",
        "i18n": listing_i18n(manifest),
        **runtime_fields(manifest),
    }


def read_manifest(package: bytes) -> dict | None:
    """The plugin.json inside a package (at its root, or in its one top-level folder).

    Args:
        package: The package's bytes.

    Returns:
        The manifest, or None when the package has no readable one.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as z:
            names = [n for n in z.namelist() if n == "plugin.json" or (n.count("/") == 1 and n.endswith("/plugin.json"))]
            if len(names) != 1:
                return None
            # The header's size is the package's say-so: read at most a megabyte, whatever it says.
            with z.open(names[0]) as f:
                raw = f.read((1 << 20) + 1)
            if len(raw) > 1 << 20:
                return None
            m = json.loads(raw.decode("utf-8"))
            return m if isinstance(m, dict) else None
    except (zipfile.BadZipFile, ValueError, UnicodeDecodeError, KeyError):
        return None


def package_refs(e: object) -> dict | None:
    """The packages a registry entry names, each with its `package`, `sha256` and `size`.

    Args:
        e: A registry entry (untrusted).

    Returns:
        The entry itself under None for a plugin with one package, or its packages by
        platform; None when the entry names its packages in neither way.
    """
    if not isinstance(e, dict):
        return None
    by_platform = e.get("packages")
    if by_platform is None:
        return {None: e} if isinstance(e.get("package"), str) else None
    # One way or the other: an entry with both would list a package the checks never read.
    if not isinstance(by_platform, dict) or not by_platform or not set(by_platform) <= set(PLATFORMS) or {"package", "sha256", "size"} & set(e):
        return None
    if not all(isinstance(r, dict) and set(r) == {"package", "sha256", "size"} and isinstance(r["package"], str) for r in by_platform.values()):
        return None
    return dict(sorted(by_platform.items()))


def site(data: Path, pr: str, sha: str, out: Path, published: dict | None = None) -> int:
    """Builds the site the deploy job publishes, trusting nothing in `data`.

    Args:
        data: The preview data from the pull request's CI run (untrusted).
        pr: The pull request number.
        sha: The commit the preview was built from.
        out: The folder to write the site to (replaced).
        published: The published registry's entries by id; when given, they list every plugin
            the pull request doesn't build, in place of the artifact's entries.

    Returns:
        The process exit code (1 when the data doesn't check out).
    """
    if not pr.isdigit() or not re.fullmatch(r"[0-9a-f]{40}", sha):
        print("preview: bad pull request number or commit", file=sys.stderr)
        return 1
    if out.exists():
        shutil.rmtree(out)
    (out / "packages").mkdir(parents=True)
    reg_path = data / "registry.json"
    if not reg_path.is_file() or reg_path.is_symlink() or reg_path.stat().st_size > MAX_REGISTRY:
        print("preview: registry.json is missing or too large", file=sys.stderr)
        return 1
    try:
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        print("preview: registry.json isn't valid JSON", file=sys.stderr)
        return 1
    plugins = registry.get("plugins") if isinstance(registry, dict) else None
    if not isinstance(plugins, list):
        print("preview: registry.json has no plugins list", file=sys.stderr)
        return 1
    if (data / "packages").is_symlink():
        print("preview: the packages folder is a link", file=sys.stderr)
        return 1
    packages = (data / "packages").resolve()
    kept: list = []
    previewed = []
    total = 0
    for e in plugins:
        # The entry's packages: its only one (under None), or one per platform.
        refs = package_refs(e)
        if refs is None:
            print("preview: an entry with no package", file=sys.stderr)
            return 1
        # Every package is either one this preview holds or a published one: a pull request
        # can't point an entry anywhere else. Each is checked on its own: a plugin can add one
        # platform's package to those already published.
        bodies = []
        for platform, r in refs.items():
            pkg = r["package"]
            if pkg.startswith(PUBLISHED):
                name = pkg.removeprefix(PUBLISHED)
                named = name.split("/", 1)[0] == e.get("id") and name.endswith(f"-{platform}.rgplugin") if platform else True
                if not PACKAGE_RE.fullmatch(name) or not named:
                    print(f"preview: {pkg!r} isn't a published package's address", file=sys.stderr)
                    return 1
                continue
            if not pkg.startswith("packages/"):
                print(f"preview: {pkg!r} is neither in this preview nor published", file=sys.stderr)
                return 1
            name = pkg.removeprefix("packages/")
            src = data / "packages" / name
            # Inside the preview's packages, whatever links the path goes through.
            inside = src.resolve().is_relative_to(packages)
            if not PACKAGE_RE.fullmatch(name) or not inside or not src.is_file() or src.is_symlink() or src.stat().st_size > MAX_PACKAGE:
                print(f"preview: {pkg!r} is not a package this preview holds", file=sys.stderr)
                return 1
            body = src.read_bytes()
            if hashlib.sha256(body).hexdigest() != r.get("sha256") or len(body) != r.get("size"):
                print(f"preview: {pkg!r} doesn't match its registry entry's sha256 and size", file=sys.stderr)
                return 1
            # The entry is what build.py makes from the package's own plugin.json, under the package's
            # id: only its categories come from elsewhere. A platform's package is named for it.
            manifest = read_manifest(body)
            cats = e.get("categories", [])
            good_cats = isinstance(cats, list) and len(cats) <= 4 and all(isinstance(c, str) and len(c) <= 32 for c in cats)
            good_name = platform is None or name.endswith(f"-{platform}.rgplugin")
            if manifest is None or name.split("/", 1)[0] != e.get("id") or not good_cats or not good_name or e != entry_for(manifest, e, None if platform else pkg):
                print(f"preview: the entry for {pkg!r} doesn't describe the package it names", file=sys.stderr)
                return 1
            bodies.append((name, body))
        # Nothing of it is in this preview: listed as published.
        if not bodies:
            kept.append(e)
            continue
        # Each plugin once, however often the registry lists it.
        if any(p.get("id") == e.get("id") for p in previewed):
            continue
        previewed.append(e)
        for name, body in bodies:
            total += len(body)
            if total > MAX_SITE:
                print(f"preview: the packages add up to more than {MAX_SITE >> 20} MiB", file=sys.stderr)
                return 1
            dest = out / "packages" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
    if published is not None:
        # Every plugin the pull request doesn't build is listed as it is published, whatever the
        # artifact says about it (and however far main has moved since the pull request's base).
        built = {e.get("id") for e in previewed}
        registry = {"version": 1, "plugins": previewed + [e for i, e in published.items() if i not in built]}
    else:
        registry = {"version": 1, "plugins": previewed + kept}
    registry["plugins"].sort(key=lambda e: str(e.get("name", "")).lower())
    (out / "registry.json").write_text(json.dumps(registry, ensure_ascii=False, separators=(",", ":")) + "\n")
    # RustGIS in the browser fetches the registry and packages from another origin.
    (out / "_headers").write_text("/*\n  Access-Control-Allow-Origin: *\n  X-Content-Type-Options: nosniff\n")
    (out / "index.html").write_text(page(pr, sha, previewed))
    print(f"preview site: {len(previewed)} plugin(s) for #{pr}")
    return 0


def page(pr: str, sha: str, previewed: list[dict]) -> str:
    """The landing page: what the preview holds and a link that opens it in RustGIS.

    Args:
        pr: The pull request number.
        sha: The commit the preview was built from.
        previewed: The registry entries of the pull request's plugins.

    Returns:
        The page's HTML.
    """
    rows = "\n".join(
        f"<li><b>{html.escape(str(e.get('name', '')))}</b> {html.escape(str(e.get('version', '')))}"
        f" <code>{html.escape(str(e.get('id', '')))}</code><br>{html.escape(str(e.get('description', '')))}"
        f"<br><small>Permissions: {html.escape(', '.join(map(str, e.get('permissions') or [])) or 'none')}</small>"
        + ("<br><small><b>Not sandboxed:</b> it runs as a program on your computer, in the desktop app and rustgis-cli only.</small>" if e.get("runtime") == "process" else "")
        + "</li>"
        for e in previewed
    )
    repo = "https://github.com/opengeos/rustgis-plugins"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>RustGIS plugin preview #{pr}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1d2330}}
a.open{{display:inline-block;background:#2563eb;color:#fff;padding:.6rem 1rem;border-radius:.4rem;text-decoration:none;font-weight:600}}
li{{margin:.6rem 0}} code{{background:#eef1f5;padding:0 .3rem;border-radius:.2rem}} .warn{{background:#fff7e6;border:1px solid #f0c36d;padding:.6rem .8rem;border-radius:.4rem}}</style>
</head><body>
<h1>Plugin preview for <a href="{repo}/pull/{pr}">#{pr}</a></h1>
<p>Built from <a href="{repo}/commit/{sha}"><code>{sha[:7]}</code></a>. This preview's registry lists every RustGIS plugin, with this pull request's
plugins served from here:</p>
<ul>{rows}</ul>
<p><a class="open" id="open" href="{WEB_APP}">Open in RustGIS</a></p>
<p>It opens <a href="{WEB_APP}">web.rustgis.app</a>'s Plugin Manager on this preview's registry. Install a plugin there; RustGIS shows
its permissions first. The registry is only used for that visit. On the desktop, set <b>Plugins › Plugin Manager › Settings › Plugin
registry</b> to <code id="reg">registry.json</code> here.</p>
<p class="warn">This is unreviewed code from a pull request. Install it only if you are reviewing that pull request.</p>
<script>
const reg = new URL("registry.json", location.href).href;
document.getElementById("reg").textContent = reg;
document.getElementById("open").href = "{WEB_APP}?registry=" + encodeURIComponent(reg);
</script>
</body></html>
"""


def main() -> int:
    """Runs the command line.

    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", nargs=4, metavar=("DATA", "PR", "SHA", "OUT"), help="build the deploy job's site from preview data")
    ap.add_argument("--published", metavar="REGISTRY", help="with --site: the published registry.json, which lists the other plugins")
    ap.add_argument("paths", nargs="*", metavar="DIST OUT", help="make preview data from a registry build")
    args = ap.parse_args()
    if args.site:
        data, pr, sha, out = args.site
        published = None
        if args.published:
            entries = json.loads(Path(args.published).read_text(encoding="utf-8")).get("plugins", [])
            published = {e.get("id"): e for e in entries if isinstance(e, dict)}
        return site(Path(data), pr, sha, Path(out), published)
    if len(args.paths) != 2:
        ap.error("give DIST and OUT, or --site DATA PR SHA OUT")
    return make(Path(args.paths[0]), Path(args.paths[1]))


if __name__ == "__main__":
    sys.exit(main())
