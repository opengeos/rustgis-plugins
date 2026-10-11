#!/usr/bin/env python3
"""Turns a registry build into a pull request preview that RustGIS can install from.

    python scripts/preview.py dist preview                        # in CI, after build.py
    python scripts/preview.py --site preview <pr> <sha> <out>     # the deploy job's site

The first form makes the preview's data from `dist/` (what `build.py` wrote): `registry.json`
lists every plugin, as the real registry does, except that the packages this pull request built
(`dist/new.txt`) are served from the preview itself, by a path relative to the registry. Those
packages are copied beside it, and `preview.json` names the plugins they hold.

The second form is run by the deploy job on the base branch's code. It copies only the
registry and the packages out of the (untrusted) preview data, checking each, and writes the
landing page and headers itself, so a pull request can't publish pages of its own.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

PACKAGE_RE = re.compile(r"^([a-z][a-z0-9-]{0,63})/\1-[0-9A-Za-z.+-]{1,64}\.rgplugin$")
MAX_PACKAGE = 64 * 1024 * 1024
MAX_REGISTRY = 32 * 1024 * 1024
WEB_APP = "https://web.rustgis.app/"
PUBLISHED = "https://packages.rustgis.app/"


def make(dist: Path, out: Path) -> int:
    """Writes the preview's data (registry, packages, preview.json) from a registry build.

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
        if not PACKAGE_RE.match(name):
            print(f"preview: skipping unexpected package name {name!r}", file=sys.stderr)
            continue
        dest = out / "packages" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dist / "packages" / name, dest)
        pid = name.split("/", 1)[0]
        for e in registry.get("plugins", []):
            if e.get("id") == pid and e.get("package", "").endswith("/" + name):
                # Relative to registry.json, which RustGIS resolves against the registry's address.
                e["package"] = f"packages/{name}"
                previewed.append({"id": pid, "name": e.get("name", pid), "version": e.get("version", "")})
    (out / "registry.json").write_text(json.dumps(registry, ensure_ascii=False, separators=(",", ":")) + "\n")
    (out / "preview.json").write_text(json.dumps({"plugins": previewed}, ensure_ascii=False) + "\n")
    print(f"preview: {len(previewed)} plugin(s) from this pull request: {', '.join(p['id'] for p in previewed) or 'none'}")
    return 0


def site(data: Path, pr: str, sha: str, out: Path) -> int:
    """Builds the site the deploy job publishes, trusting nothing in `data`.

    Args:
        data: The preview data from the pull request's CI run (untrusted).
        pr: The pull request number.
        sha: The commit the preview was built from.
        out: The folder to write the site to (replaced).

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
    registry = json.loads(reg_path.read_text())
    plugins = registry.get("plugins") if isinstance(registry, dict) else None
    if not isinstance(plugins, list):
        print("preview: registry.json has no plugins list", file=sys.stderr)
        return 1
    previewed = []
    for e in plugins:
        pkg = e.get("package", "") if isinstance(e, dict) else ""
        if not isinstance(pkg, str):
            print("preview: an entry with no package", file=sys.stderr)
            return 1
        # Every package is either one this preview holds or a published one: a pull request
        # can't point an entry anywhere else.
        if pkg.startswith(PUBLISHED):
            name = pkg.removeprefix(PUBLISHED)
            if not PACKAGE_RE.match(name):
                print(f"preview: {pkg!r} isn't a published package's address", file=sys.stderr)
                return 1
            continue
        if not pkg.startswith("packages/"):
            print(f"preview: {pkg!r} is neither in this preview nor published", file=sys.stderr)
            return 1
        name = pkg.removeprefix("packages/")
        src = data / "packages" / name
        if not PACKAGE_RE.match(name) or not src.is_file() or src.is_symlink() or src.stat().st_size > MAX_PACKAGE:
            print(f"preview: dropping {pkg!r}: not a package this preview holds", file=sys.stderr)
            return 1
        dest = out / "packages" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        previewed.append(e)
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
        f"<br><small>Permissions: {html.escape(', '.join(map(str, e.get('permissions') or [])) or 'none')}</small></li>"
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
<ul>{rows or "<li>No plugin packages changed in this pull request.</li>"}</ul>
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
    ap.add_argument("paths", nargs="*", metavar="DIST OUT", help="make preview data from a registry build")
    args = ap.parse_args()
    if args.site:
        data, pr, sha, out = args.site
        return site(Path(data), pr, sha, Path(out))
    if len(args.paths) != 2:
        ap.error("give DIST and OUT, or --site DATA PR SHA OUT")
    return make(Path(args.paths[0]), Path(args.paths[1]))


if __name__ == "__main__":
    sys.exit(main())
