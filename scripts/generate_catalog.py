#!/usr/bin/env python3
"""Generates the plugin catalog pages from the built registry.

    python scripts/build.py && python scripts/generate_catalog.py

Writes docs/catalog.md, which shows the plugins as cards with a search box and category filters
(docs/javascripts/catalog.js), and one page per plugin in docs/catalog/<id>.md. The pages list
each plugin's commands and tools (read from its package) and how to install it. Both are
generated, not committed.

Registry text comes from contributors' pull requests, so it is escaped. HTML and Markdown in it
are shown as typed and never take effect.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
DOCS = ROOT / "docs"
PLUGIN_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~<>])")
KINDS = {
    "layer": "Layer",
    "vectorLayer": "Vector layer",
    "polygonLayer": "Polygon layer",
    "pointLayer": "Point layer",
    "field": "Field",
    "numericField": "Numeric field",
    "number": "Number",
    "text": "Text",
    "choice": "Choice",
    "bool": "Yes or no",
}
PERMISSIONS = {
    "edit": ("Edits the project", "Can change the project: run commands that edit it and add layers."),
    "files": ("Reads and writes files", "Can run commands that read or write files on your computer."),
}


def text(value: object) -> str:
    """Escapes registry text so it renders literally in Markdown.

    Args:
        value: A string from the registry or a manifest.

    Returns:
        The text on one line, with HTML escaped and Markdown punctuation backslash-escaped.
    """
    one_line = " ".join(str(value).split())
    return MARKDOWN_SPECIAL.sub(r"\\\1", html.escape(one_line, quote=False))


def safe_url(value: object) -> str | None:
    """Returns an http(s) URL fit for a Markdown link, or None.

    Args:
        value: A URL from the registry.

    Returns:
        The URL with unsafe characters percent-encoded, or None unless it is http(s).
    """
    url = str(value or "").strip()
    if not re.match(r"^https?://", url):
        return None
    return quote(url, safe=":/?#@!$&'*+,;=%~")


def link(label: str, url: object, attrs: str = "") -> str | None:
    """Renders a Markdown link to a registry URL.

    Args:
        label: The link text (already Markdown).
        url: The URL.
        attrs: An attribute list such as ``{ target=_blank }``.

    Returns:
        The link, or None when the URL isn't http(s).
    """
    href = safe_url(url)
    return f"[{label}]({href}){attrs}" if href else None


def tags(categories: list[object]) -> str:
    """Renders categories as code-span tags (plain words only).

    Args:
        categories: The entry's categories.

    Returns:
        The tags separated by spaces.
    """
    return " ".join(f"`{c}`" for c in categories if isinstance(c, str) and re.fullmatch(r"[A-Za-z][A-Za-z ]*", c))


def load_manifests() -> dict:
    """Each plugin's manifest, from the package index the build wrote (dist/packages.json).

    Returns:
        Manifests by plugin id.
    """
    index = json.loads((DIST / "packages.json").read_text())
    return {p["id"]: p.get("manifest") or {} for p in index.get("plugins", [])}


def size(n: int) -> str:
    """A package size in KB or MB.

    Args:
        n: Bytes.

    Returns:
        The size, readable.
    """
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{max(1, round(n / 1024))} KB"


def render_catalog(count: int) -> str:
    """Renders docs/catalog.md: a shell that catalog.js fills from registry.json, so the page stays
    small however many plugins there are (cards load in batches as you scroll).

    Args:
        count: How many plugins the registry lists.

    Returns:
        The page.
    """
    head = (
        "---\nhide:\n  - toc\n---\n\n# Plugin catalog\n\n"
        f"There {'is' if count == 1 else 'are'} **{count:,}** {'plugin' if count == 1 else 'plugins'} in the registry. "
        "To install one in RustGIS, open **Plugins › Plugin Manager › Browse** and choose **Install**. "
        "RustGIS shows what the plugin may do before installing it. "
        "On the command line, run `rustgis-cli plugins get <id>`.\n\n"
    )
    if count == 0:
        return head + "_The registry is empty._\n"
    return head + (
        '<div class="plugin-filters" hidden>\n'
        '  <input type="search" class="plugin-search" placeholder="Search plugins, commands and tools" aria-label="Search plugins">\n'
        '  <div class="plugin-categories" role="group" aria-label="Filter by category"></div>\n'
        '  <p class="plugin-count" aria-live="polite"></p>\n'
        "</div>\n"
        '<div class="grid cards plugin-catalog" data-registry="/registry.json"><ul></ul></div>\n'
        '<div class="plugin-more" aria-hidden="true"></div>\n'
        "<noscript><p>The catalog needs JavaScript. The plugin list is also available as "
        '<a href="/registry.json">registry.json</a>.</p></noscript>\n'
    )


def render_page(entry: dict, manifest: dict) -> str:
    """Renders docs/catalog/<id>.md: one plugin's details, commands, tools and how to install it.

    Args:
        entry: A registry.json entry.
        manifest: Its manifest.

    Returns:
        The page.
    """
    pid = entry["id"]
    name = entry.get("name", pid)
    front = f"---\ntitle: {json.dumps(html.escape(name))}\n"
    if entry.get("description"):
        front += f"description: {json.dumps(html.escape(' '.join(entry['description'].split())))}\n"
    # Kept out of the site search: the catalog searches plugins itself, and thousands of pages
    # would make the search index every visitor downloads several megabytes.
    front += "search:\n  exclude: true\n---\n\n"
    buttons = [
        f"[:material-download: Download {text(entry.get('version', ''))}]({safe_url(entry['package'])}){{ .md-button .md-button--primary }}"
    ]
    home = link(":octicons-mark-github-16: Homepage", entry.get("homepage"), "{ .md-button target=_blank }")
    if home:
        buttons.append(home)
    rows = [("Plugin id", f"`{pid}`"), ("Version", text(entry.get("version", "")))]
    if entry.get("author"):
        rows.append(("Author", text(entry["author"])))
    if entry.get("license"):
        rows.append(("License", text(entry["license"])))
    if entry.get("minRustgisVersion"):
        rows.append(("Requires", f"RustGIS {text(entry['minRustgisVersion'])} or newer"))
    if tags(entry.get("categories") or []):
        rows.append(("Categories", tags(entry["categories"])))
    rows.append(("Package", f"{size(int(entry.get('size', 0)))}, SHA-256 `{entry.get('sha256', '')}`"))
    body = [f"# {text(name)}", ""]
    if entry.get("description"):
        body += [text(entry["description"]), ""]
    body += [" ".join(buttons), "", *(f"- **{k}:** {v}" for k, v in rows), ""]

    body += ["## Permissions", ""]
    perms = [p for p in entry.get("permissions") or [] if p in PERMISSIONS]
    body += ["- **Reads the map:** its layers, their features and the view. Every plugin can."]
    body += [f"- **{PERMISSIONS[p][0]}:** {PERMISSIONS[p][1]}" for p in perms]
    body += ["", "Plugins run in a sandbox with no other access to your computer or the network.", ""]

    commands = manifest.get("commands") or []
    tab = manifest.get("tab") or {}
    where = (
        f"On the plugin's own **{text(tab['label'])}** tab, and in command search."
        if tab.get("label")
        else "On the **Plugins** tab, in a group named after the plugin, and in command search."
    )
    if commands:
        body += ["## Commands", "", where, ""]
        body += ["| Command | Id | What it does |", "|---|---|---|"]
        for c in commands:
            cid = str(c.get("id", ""))
            if PLUGIN_ID.fullmatch(cid):
                body.append(f"| {text(c.get('label', cid))} | `plugin:{pid}/{cid}` | {text(c.get('help', ''))} |")
        body.append("")
    tools = manifest.get("tools") or []
    if tools:
        body += ["## Tools", "", "In the **Geoprocessing** pane under **Plugins**, and on the **Plugins** tab.", ""]
        for t in tools:
            tid = str(t.get("id", ""))
            if not PLUGIN_ID.fullmatch(tid):
                continue
            body += [f"### {text(t.get('name', tid))}", "", f"`plugin:{pid}/{tid}`", ""]
            if t.get("summary"):
                body += [text(t["summary"]), ""]
            params = t.get("params") or []
            if params:
                body += ["| Parameter | Kind | Default | Description |", "|---|---|---|---|"]
                for p in params:
                    req = " (required)" if p.get("required") else ""
                    default = f"`{text(json.dumps(p['default']))}`" if "default" in p else ""
                    kind = KINDS.get(str(p.get("kind", "")), text(p.get("kind", "")))
                    if p.get("choices"):
                        kind += ": " + ", ".join(text(c) for c in p["choices"])
                    body.append(f"| {text(p.get('label', p.get('name', '')))}{req} | {kind} | {default} | {text(p.get('help', ''))} |")
                body.append("")

    body += [
        "## Install",
        "",
        "- **In RustGIS** (desktop or [web](https://web.rustgis.app)): open **Plugins › Plugin Manager › Browse**, find "
        f"**{text(name)}** and choose **Install**. RustGIS checks the download against the SHA-256 above.",
        f"- **On the command line:** `rustgis-cli plugins get {pid}`.",
        "- **From the file:** download the package, then choose **Plugins › Install From File** in RustGIS or run "
        "`rustgis-cli plugins install <file>`.",
        "",
    ]
    example = next((t for t in tools if PLUGIN_ID.fullmatch(str(t.get("id", ""))) and t.get("params")), None)
    if example:
        params = {}
        for p in example["params"]:
            if p.get("kind") in ("layer", "vectorLayer", "polygonLayer"):
                params[p["name"]] = "Countries"
            elif p.get("kind") == "pointLayer":
                params[p["name"]] = "Cities"
            elif "default" in p:
                params[p["name"]] = p["default"]
        cmd = json.dumps({"tool": f"plugin:{pid}/{example['id']}", "params": params})
        body += [
            "Run a tool on the World Atlas sample:",
            "",
            "```sh",
            f"rustgis-cli run --sample --cmd 'gp.run={cmd}' --cmd 'share.exportMap={{\"path\":\"map.png\"}}'",
            "```",
            "",
        ]
    body += ["[:material-arrow-left: All plugins](../catalog.md)", ""]
    return front + "\n".join(body)


def main() -> None:
    """Writes docs/catalog.md and docs/catalog/<id>.md from dist/registry.json."""
    registry = json.loads((DIST / "registry.json").read_text())
    manifests = load_manifests()
    entries = []
    for e in registry.get("plugins", []):
        if not PLUGIN_ID.fullmatch(str(e.get("id", ""))) or not safe_url(e.get("package")):
            raise SystemExit(f"refusing to write a page for {e.get('id')!r}")
        entries.append((e, manifests.get(e["id"], {})))
    (DOCS / "catalog.md").write_text(render_catalog(len(entries)))
    pages = DOCS / "catalog"
    pages.mkdir(exist_ok=True)
    for old in pages.glob("*.md"):
        old.unlink()
    for e, m in entries:
        (pages / f"{e['id']}.md").write_text(render_page(e, m))
    print(f"wrote docs/catalog.md and {len(entries)} plugin pages")


if __name__ == "__main__":
    main()
