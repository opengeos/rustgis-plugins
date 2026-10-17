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
| [`plugins/basemaps/`](plugins/basemaps) | **Basemaps**: a tab of keyless public basemaps (OpenStreetMap, Esri, USGS, NASA, OpenFreeMap and VersaTiles vector maps) and overlays |
| [`plugins/map-explorer/`](plugins/map-explorer) | **Map Explorer**: a ribbon tab of its own (groups, a menu, a tool button) for jumping around the map, selecting and summarizing |
| [`plugins/ide-chile/`](plugins/ide-chile) | **IDE Chile**: the public geospatial services of Chile (Geoportal de Chile, IDE MINAGRI, IDE MINVU, INE Census 2017) as one-click layers, map images or features, in a tab of their own |
| [`registry/`](registry) | one file per listed plugin |
| [`blocklist.json`](blocklist.json) | withdrawn plugins |
| [`scripts/build.py`](scripts/build.py) | builds, checks and packages what changed into `dist/` |
| [`scripts/upload.py`](scripts/upload.py) | uploads new packages to `packages.rustgis.app` |
| [`scripts/smoke.py`](scripts/smoke.py) | runs new packages (or `--all`) in `rustgis-cli` |
| [`scripts/preview.py`](scripts/preview.py) | makes a pull request's plugin preview from `dist/` |
| [`docs/`](docs) | the site at plugins.rustgis.app |

## Build

The scripts need Python 3.11 or later.

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

- **Two hosts:**
  - `plugins.rustgis.app` (GitHub Pages; the `CNAME` file, with DNS a CNAME to `opengeos.github.io`) serves the site, `registry.json`, `blocklist.json` and `packages.json`.
  - `packages.rustgis.app` serves the packages from the `rustgis-plugins` Cloudflare R2 bucket, at `<id>/<id>-<version>.rgplugin`. Packages never change once published, and every version stays available.
- **Incremental builds:** each push to `main` builds only what changed. `scripts/build.py` reads the previous deploy's `packages.json` and builds or downloads only plugins whose version or source changed. An in-repo plugin whose files changed without a version bump fails the check.
- **Upload before deploy:** `scripts/upload.py` puts the new packages in the bucket, checking each one back, before the registry is deployed. It needs the `CLOUDFLARE_R2_TOKEN` secret (a Cloudflare API token with *Workers R2 Storage: Edit*) and `CLOUDFLARE_ACCOUNT_ID`.
- **Previews:** each pull request's new packages are published to Cloudflare Pages (`pr-<N>.rustgis-plugins-preview.pages.dev`, the `rustgis-plugins-preview` project) by `plugin-preview.yml`, with an **Open in RustGIS** link that opens web.rustgis.app on the preview's registry (or paste the preview's `registry.json` into **Plugin Manager › Settings › Plugin registry**). The non-preview plugins are listed as published. It needs the `CLOUDFLARE_PAGES_TOKEN` secret (an API token with *Cloudflare Pages: Edit*).
- **CI:** pull requests build and run only the plugins they change. The **Nightly** workflow runs every published plugin in the latest RustGIS.

Code is MIT licensed. Each plugin states its own license in its `plugin.json`.
