# Publish a plugin

The registry is curated: plugins run inside people's RustGIS, so each one is reviewed before it
is listed. To add yours, open a pull request on
[opengeos/rustgis-plugins](https://github.com/opengeos/rustgis-plugins) that adds
`registry/<id>.json`, named after the plugin's id.

## A plugin in this repository

Put its source in `plugins/<folder>/`, with `plugin.json`, `Cargo.toml` and `src/`, and add the
folder to the workspace. Then add:

```json
{ "id": "my-plugin", "path": "plugins/my-plugin", "categories": ["Vector"] }
```

CI builds the module from source and packages it.

## A plugin published elsewhere

Attach a `.rgplugin` package to a release in your own repository, and add:

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

## What CI checks

`python scripts/build.py --check` checks:

- the manifest: id, version, permissions, commands, tools and parameter kinds;
- that the module exports what RustGIS needs and imports nothing else (no WASI, no
  JavaScript glue);
- that the package's id matches its registry file.

It writes `dist/registry.json`. Then `python scripts/smoke.py` loads every package into the real
`rustgis-cli` and runs its tools on the World Atlas sample.

An automated Claude Code review also comments on each pull request: on the permissions, the
commands the plugin runs and its manifest, and, for a package published elsewhere, on what the
package holds. A first-time contributor's pull request is reviewed once a maintainer comments
`/claude-review` or adds the `claude-review` label. A maintainer still reviews every plugin
before it is merged.

Name, version, description, author, homepage, license and permissions all come from the
package's `plugin.json`, so the listing always matches what is installed. `categories` takes one
to four of: Analysis, Cartography, Data, Editing, Example, Hydrology, Imagery, Raster, Sampling,
Terrain, Utilities, Vector, Visualization.

## Updating and withdrawing

Bump `version` in `plugin.json` (and `source` for a plugin hosted elsewhere). RustGIS shows an
**Update** button to everyone with an older version. Published packages never change, so CI
refuses an in-repository plugin whose files changed without a version bump. This covers the
plugin's own folder (its source, `plugin.json` and README), compared with what was last
published. A change to the shared SDK or the workspace lockfile doesn't republish existing
versions; bump a plugin's version to ship it. Every published
version stays available at its URL.

Maintainers withdraw a plugin by adding it to `blocklist.json`. `{"id": "...", "reason": "..."}`
blocks every version; adding `"sha256"` blocks only that package. RustGIS turns blocked plugins
off and refuses to install them again. Report a malicious plugin privately (see
[SECURITY.md](https://github.com/opengeos/rustgis-plugins/blob/main/SECURITY.md)).

## registry.json

Each entry has `id`, `name`, `version`, `description`, `author`, `homepage`, `license`,
`categories`, `package` (the package's URL on `packages.rustgis.app`), `sha256`, `size`,
`minRustgisVersion`, `permissions`, the labels of its `commands`, the names of its `tools` and
its `tab`.

`packages.json` beside it is the build's index: each plugin's package, full manifest and where it
came from. The next build reads it to rebuild only what changed.
