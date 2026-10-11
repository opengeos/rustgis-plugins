# RustGIS Plugins

Plugins for [RustGIS](https://github.com/opengeos/RustGIS), the plugin SDK, and the plugin
registry published at **[plugins.rustgis.app](https://plugins.rustgis.app)**.

A RustGIS plugin is a WebAssembly module with a `plugin.json` manifest, packaged as a
`.rgplugin` file. It adds commands, which appear on the **Plugins** tab, in command search and
on the command line, and geoprocessing tools. It runs sandboxed in the desktop app, in
`rustgis-cli` and in the [web app](https://web.rustgis.app). See
[RustGIS's plugin documentation](https://github.com/opengeos/RustGIS/blob/main/docs/plugins.md)
for the design, the permissions and the ABI.

| Folder | What |
|---|---|
| [`sdk/`](sdk) | `rustgis-plugin`, the Rust SDK for writing plugins |
| [`plugins/hello/`](plugins/hello) | **Hello RustGIS**, a starter plugin to copy: commands and a tool |
| [`plugins/vector-toolkit/`](plugins/vector-toolkit) | **Vector Toolkit**: hexagon and square grids, random points in polygons |
| [`plugins/map-explorer/`](plugins/map-explorer) | **Map Explorer**: a ribbon tab of its own (groups, a menu, a tool button) for jumping around the map, selecting and summarizing |
| [`registry/`](registry) | one file per listed plugin |
| [`blocklist.json`](blocklist.json) | withdrawn plugins |
| [`scripts/build.py`](scripts/build.py) | builds, checks and packages everything into `dist/` |
| [`scripts/smoke.py`](scripts/smoke.py) | runs every package in `rustgis-cli` |
| [`docs/`](docs) | the site at plugins.rustgis.app |

## Build

```sh
rustup target add wasm32-unknown-unknown
python scripts/build.py --check        # dist/registry.json and dist/packages/
python scripts/smoke.py --cli path/to/rustgis-cli
```

To try the local registry in RustGIS, serve `dist/` (`python -m http.server -d dist 8000`). Then
set **Plugins › Plugin Manager › Settings › Plugin registry** to
`http://localhost:8000/registry.json`.

## Add a plugin

See [Write a plugin](https://plugins.rustgis.app/develop/) and
[Publish a plugin](https://plugins.rustgis.app/registry/). In short: open a pull request that adds
`registry/<id>.json`, pointing either at a folder in `plugins/` or at a `.rgplugin` you publish,
with its SHA-256.

## Deployment

Each push to `main` builds the registry and the docs and publishes them to GitHub Pages at
`plugins.rustgis.app` (the `CNAME` file; DNS is a CNAME to `opengeos.github.io`).

Code is MIT licensed. Each plugin states its own license in its `plugin.json`.
