# Publish a plugin

The registry is curated: plugins run inside people's RustGIS, so each one is reviewed before it
is listed. The plugin's source stays in your own repository: you release its `.rgplugin`
there, and the registry lists it. To add yours, open a pull request on
[opengeos/rustgis-plugins](https://github.com/opengeos/rustgis-plugins) that adds one file,
`registry/<id>.json`, named after the plugin's id. Pull requests that add plugin source code
here aren't accepted.

## Your registry entry

Start from the [Rust WebAssembly template](https://github.com/opengeos/rustgis-plugin-template),
[Rust process template](https://github.com/opengeos/rustgis-rust-process-plugin-template) or
[Python process template](https://github.com/opengeos/rustgis-python-plugin-template): their Release
workflow attaches the `.rgplugin` to a GitHub release and writes this entry for you. Or attach a
`.rgplugin` package to a release in your own repository yourself, and add:

```json
{
  "id": "my-plugin",
  "source": {
    "url": "https://github.com/you/my-plugin/releases/download/v1.0.0/my-plugin-1.0.0.rgplugin",
    "sha256": "<sha256sum my-plugin-1.0.0.rgplugin>"
  },
  "categories": ["Analysis"]
}
```

CI downloads the package, checks the hash, and validates it. Once merged, the package is served
from `packages.rustgis.app/my-plugin/my-plugin-<version>.rgplugin` with that hash, so later
edits to your release don't change what people install. A new version needs a new `version` and
a new `source`.

<a id="a-process-plugin-python"></a>

## A process plugin (Rust or Python)

A [process plugin](https://github.com/opengeos/RustGIS/blob/main/docs/plugins.md#process-plugins) (`"runtime": "process"`) is a program instead of a WebAssembly
module, such as a native Rust executable or a Python script, that speaks the plugin protocol
over its standard input and output. The
[Rust process template](https://github.com/opengeos/rustgis-rust-process-plugin-template) provides
a command, a tool and JSON-line transport, and releases one package containing Linux, Windows
and universal macOS executables. Users run it without Rust or Python installed. The
[Python process template](https://github.com/opengeos/rustgis-python-plugin-template) packages a
Python program and declares its managed environment. Both attach a `.rgplugin` and its registry
entry to a release. List either like any other plugin, with a `source`.

Process plugins are accepted, with two differences:

- **They are not sandboxed.** The program can do anything its user can, whatever its
  permissions, so it is reviewed more strictly: the reviewer reads all of its code (every file
  in the package), its requirements and what it runs. Keep the package to source and data you
  can explain; a prebuilt executable needs a link to its source. The
  catalog and RustGIS's Plugin Manager mark it **Not sandboxed**.
- **They run on the desktop and the command line only**, not in the web app (or a preview's
  **Open in RustGIS** link: try one in the desktop app instead).

CI checks the package's files in place of a module:

- `run` is a program and its arguments (a list of strings), or an object of them by platform
  (`windows`, `macos`, `linux`, `default`). A program named by a relative path (`bin/tool`) is
  in the package, and executable unless it is for Windows only (under `windows`, or a `.exe`,
  `.bat` or `.cmd`); the script a `python` command runs (`["python", "main.py"]`) is in the
  package too. A bare name (`python3`, `node`) is looked up on PATH, not in the package. A
  full path to a program isn't accepted. The execute bit is the file's Unix mode
  in the zip: use a packaging tool that preserves it, such as the templates' packaging scripts,
  including when building on Windows.
- `python`, when given, has a `version` like `3.12` or `3.12.4`, its `requirements` file is in
  the package, and `run` starts `python`, which RustGIS swaps for the environment's interpreter.
- There is no `entry`, the package holds at most 4,096 files, and they unpack to at most 1 GB.

The smoke test installs the package with `rustgis-cli plugins install` (which prepares a Python
environment with uv when the manifest declares one) and runs it on the World Atlas sample on
Linux. A plugin with no program for Linux is skipped there, so say how you tested it in the pull
request. Since CI runs a pull request's process plugin before it is reviewed, it does so in a
job of its own, with no credentials, that saves and uploads nothing. The plugin still has the
runner's network there.

## What CI checks

`python scripts/build.py --check` checks:

- the manifest: id, version, permissions, commands, tools and parameter kinds;
- that the module exports what RustGIS needs and imports nothing else (no WASI, no
  JavaScript glue), or, for a [process plugin](#a-process-plugin-rust-or-python), its `run`, `python`
  and files;
- that the package's id matches its registry file.

It writes `dist/registry.json`. Then `python scripts/smoke.py` loads every package into the real
`rustgis-cli` and runs its tools on the World Atlas sample.

An automated Claude Code review also comments on each pull request: on the permissions, the
commands the plugin runs and its manifest, and, for a package published elsewhere, on what the
package holds (all the code of a process plugin). A first-time contributor's pull request is reviewed once a maintainer comments
`/claude-review` or adds the `claude-review` label. A maintainer still reviews every plugin
before it is merged.

Name, version, description, author, homepage, license and permissions all come from the
package's `plugin.json`, so the listing always matches what is installed. `categories` takes one
to four of: Analysis, Basemaps, Cartography, Data, Editing, Example, Hydrology, Imagery, Raster,
Sampling, Terrain, Utilities, Vector, Visualization.

## Try it before it is merged

Each pull request that builds a plugin gets a preview on Cloudflare Pages, and a comment with an
**Open in RustGIS** link:

```text
https://pr-<N>.rustgis-plugins-preview.pages.dev
```

The link opens [web.rustgis.app](https://web.rustgis.app)'s Plugin Manager on the preview's
registry (its `?registry=` address parameter): every published plugin, with the pull request's
new packages served from the preview.
Install the plugin from **Browse**; RustGIS shows its permissions first. On the desktop, set
**Plugin Manager › Settings › Plugin registry** to the preview's `registry.json`. The preview is
updated on each push and removed when the pull request closes. Fork pull requests get one too:
CI builds the packages with no secrets, and a separate workflow, which runs none of the pull
request's code, publishes only the registry and the packages.

!!! warning "Previews run unreviewed code"
    Install a preview's plugin only to review that pull request.

## Updating and withdrawing

Release a new version (a new `version` in `plugin.json`), then open a pull request that
updates `source` in `registry/<id>.json` to the new release's `url` and `sha256`. RustGIS shows
an **Update** button to everyone with an older version. Published packages never change, so
CI refuses a different package under a version already published. Every published version
stays available at its URL.

Maintainers withdraw a plugin by adding it to `blocklist.json`. `{"id": "...", "reason": "..."}`
blocks every version; adding `"sha256"` blocks only that package. RustGIS turns blocked plugins
off and refuses to install them again. Report a malicious plugin privately (see
[SECURITY.md](https://github.com/opengeos/rustgis-plugins/blob/main/SECURITY.md)).

## registry.json

Each entry has `id`, `name`, `version`, `description`, `author`, `homepage`, `license`,
`categories`, `package` (the package's URL on `packages.rustgis.app`), `sha256`, `size`,
`minRustgisVersion`, `permissions`, the labels of its `commands`, the names of its `tools` and
its `tab`. A process plugin's entry also has `"runtime": "process"`, and `"python"` (the Python
version, such as `"3.12"`) when RustGIS makes it a Python environment; a WebAssembly plugin's
entry has neither.

`packages.json` beside it is the build's index: each plugin's package, full manifest and where it
came from. The next build reads it to rebuild only what changed.
