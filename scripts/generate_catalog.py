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
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
DOCS = ROOT / "docs"
SITE_URL = "https://plugins.rustgis.app"
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


def manifest_of(entry: dict) -> dict:
    """Reads plugin.json from the entry's package in dist/.

    Args:
        entry: A registry.json entry.

    Returns:
        The manifest, or an empty dict when the package can't be read.
    """
    path = DIST / str(entry.get("package", ""))
    try:
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as z:
            names = z.namelist()
            name = "plugin.json" if "plugin.json" in names else next(n for n in names if n.endswith("/plugin.json"))
            return json.loads(z.read(name))
    except (OSError, StopIteration, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
        return {}


def size(n: int) -> str:
    """A package size in KB or MB.

    Args:
        n: Bytes.

    Returns:
        The size, readable.
    """
    return f"{n / 1_048_576:.1f} MB" if n >= 1_048_576 else f"{max(1, round(n / 1024))} KB"


def what_it_adds(manifest: dict) -> str:
    """Summarizes what a plugin adds, for its card.

    Args:
        manifest: The plugin's manifest.

    Returns:
        For example "3 commands · 1 tool".
    """
    parts = []
    for key, one, many in (("commands", "command", "commands"), ("tools", "tool", "tools")):
        n = len(manifest.get(key) or [])
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    return " · ".join(parts)


def render_card(entry: dict, manifest: dict) -> str:
    """Renders one plugin as a grid card.

    Args:
        entry: A registry.json entry.
        manifest: Its manifest.

    Returns:
        The Markdown list item for the card.
    """
    name = text(entry.get("name", entry["id"]))
    meta = [f"**{text(entry.get('version', ''))}**"]
    if entry.get("author"):
        meta.append(f"by {text(entry['author'])}")
    adds = what_it_adds(manifest)
    if adds:
        meta.append(adds)
    perms = entry.get("permissions") or []
    perm_line = " ".join(
        f':material-shield-alert-outline:{{ title="{html.escape(PERMISSIONS[p][1])}" }} {PERMISSIONS[p][0]}' for p in perms if p in PERMISSIONS
    ) or ':material-shield-check-outline:{ title="Only reads the map." } Reads the map only'
    lines = [
        f"-   :material-puzzle:{{ .lg .middle }} [__{name}__](catalog/{entry['id']}.md)",
        "",
        "    ---",
        "",
    ]
    if entry.get("description"):
        lines += [f"    {text(entry['description'])}", ""]
    lines += [f'    <span class="plugin-card-meta">{" · ".join(meta)}</span>', ""]
    lines += [f'    <span class="plugin-card-perms">{perm_line}</span>', ""]
    if tags(entry.get("categories") or []):
        lines += [f"    {tags(entry['categories'])}", ""]
    links = [
        f"[:material-information-outline: Details](catalog/{entry['id']}.md)",
        link(":material-download: Download", f"{SITE_URL}/{entry['package']}"),
        link(":octicons-mark-github-16: Homepage", entry.get("homepage"), "{ target=_blank }"),
    ]
    lines += ["    " + " · ".join(x for x in links if x), ""]
    return "\n".join(lines).rstrip()


def render_filters(entries: list[dict]) -> str:
    """Renders the search box and category filters (revealed by catalog.js).

    Args:
        entries: The registry entries.

    Returns:
        The HTML block.
    """
    categories = sorted({c for e in entries for c in e.get("categories") or [] if tags([c])})
    buttons = "\n".join(
        f'    <button type="button" data-category="{html.escape(c)}" aria-pressed="false">{html.escape(c)}</button>' for c in categories
    )
    return (
        '<div class="plugin-filters" hidden>\n'
        '  <input type="search" class="plugin-search" placeholder="Search plugins, commands and tools" aria-label="Search plugins">\n'
        '  <div class="plugin-categories" role="group" aria-label="Filter by category">\n'
        f"{buttons}\n"
        "  </div>\n"
        '  <p class="plugin-count" aria-live="polite"></p>\n'
        "</div>\n\n"
    )


def render_catalog(entries: list[tuple[dict, dict]]) -> str:
    """Renders docs/catalog.md.

    Args:
        entries: (registry entry, manifest) pairs, by name.

    Returns:
        The page.
    """
    n = len(entries)
    head = (
        "---\nhide:\n  - toc\n---\n\n# Plugin catalog\n\n"
        f"There {'is' if n == 1 else 'are'} **{n}** {'plugin' if n == 1 else 'plugins'} in the registry. "
        "To install one in RustGIS, open **Plugins › Plugin Manager › Browse** and choose **Install**. "
        "RustGIS shows what the plugin may do before installing it. "
        "On the command line, run `rustgis-cli plugins get <id>`.\n\n"
    )
    if not entries:
        return head + "_The registry is empty._\n"
    cards = "\n\n".join(render_card(e, m) for e, m in entries)
    # Each card's searchable text includes its commands and tools, which the card doesn't show.
    extra = {e["id"]: " ".join([c.get("label", "") for c in m.get("commands", [])] + [t.get("name", "") for t in m.get("tools", [])]) for e, m in entries}
    data = html.escape(json.dumps(extra), quote=True)
    return head + render_filters([e for e, _ in entries]) + f'<div class="grid cards plugin-catalog" data-extra="{data}" markdown>\n\n{cards}\n\n</div>\n'


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
    front += "---\n\n"
    buttons = [
        f"[:material-download: Download {text(entry.get('version', ''))}]({SITE_URL}/{entry['package']}){{ .md-button .md-button--primary }}"
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
    if commands:
        body += ["## Commands", "", "On the **Plugins** tab, in a group named after the plugin, and in command search.", ""]
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
    entries = []
    for e in registry.get("plugins", []):
        if not PLUGIN_ID.fullmatch(str(e.get("id", ""))) or not str(e.get("package", "")).startswith("packages/"):
            raise SystemExit(f"refusing to write a page for {e.get('id')!r}")
        entries.append((e, manifest_of(e)))
    (DOCS / "catalog.md").write_text(render_catalog(entries))
    pages = DOCS / "catalog"
    pages.mkdir(exist_ok=True)
    for old in pages.glob("*.md"):
        old.unlink()
    for e, m in entries:
        (pages / f"{e['id']}.md").write_text(render_page(e, m))
    print(f"wrote docs/catalog.md and {len(entries)} plugin pages")


if __name__ == "__main__":
    main()
