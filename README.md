# RustGIS Plugins

The plugin registry of [RustGIS](https://github.com/opengeos/RustGIS), published at
**[plugins.rustgis.app](https://plugins.rustgis.app)**, and the plugin SDK.

A RustGIS plugin is a WebAssembly module (or, for a process plugin, a native Rust executable or
Python script) with a `plugin.json` manifest, packaged as a `.rgplugin` file. It adds commands,
ribbon tabs, panels and geoprocessing tools. See
[RustGIS's plugin documentation](https://github.com/opengeos/RustGIS/blob/main/docs/plugins.md)
for the design, the permissions and the ABI.

**This repository holds registry entries, not plugin source.** Each plugin lives in its own
repository and releases its `.rgplugin` there; a pull request here adds or updates one file,
`registry/<id>.json`, which points at that package and its SHA-256. Browse the plugins in the
[catalog](https://plugins.rustgis.app/catalog/).

| Where | What |
|---|---|
| [`registry/`](registry) | one entry per listed plugin |
| [`blocklist.json`](blocklist.json) | withdrawn plugins |
| [`sdk/`](sdk) | `rustgis-plugin`, the Rust SDK for WebAssembly plugins |
| [`scripts/build.py`](scripts/build.py) | checks the registry and its packages, and writes `dist/` |
| [`scripts/upload.py`](scripts/upload.py) | uploads new packages to `packages.rustgis.app` |
| [`scripts/smoke.py`](scripts/smoke.py) | runs new packages (or `--all`) in `rustgis-cli` |
| [`scripts/preview.py`](scripts/preview.py) | makes a pull request's plugin preview from `dist/` |
| [`docs/`](docs) | the site at plugins.rustgis.app |
| [rustgis-plugin-template](https://github.com/opengeos/rustgis-plugin-template) | start a sandboxed Rust WebAssembly plugin |
| [rustgis-rust-process-plugin-template](https://github.com/opengeos/rustgis-rust-process-plugin-template) | start a native Rust process plugin |
| [rustgis-python-plugin-template](https://github.com/opengeos/rustgis-python-plugin-template) | start a Python process plugin |
| [rustgis-official-plugins](https://github.com/opengeos/rustgis-official-plugins) | the RustGIS project's own plugins ([official-plugins.rustgis.app](https://official-plugins.rustgis.app)) |

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

1. Write it in its own repository, from the
   [Rust WebAssembly](https://github.com/opengeos/rustgis-plugin-template),
   [Rust process](https://github.com/opengeos/rustgis-rust-process-plugin-template) or
   [Python process](https://github.com/opengeos/rustgis-python-plugin-template) template. See
   [Write a plugin](https://plugins.rustgis.app/develop/).
2. Release it: the templates' Release workflow attaches the `.rgplugin` to a GitHub release, with
   its registry entry.
3. Open a pull request here that adds that entry as `registry/<id>.json`. See
   [Publish a plugin](https://plugins.rustgis.app/registry/).

Every plugin is listed by `source`, from a release of its own repository; `build.py` refuses a
`path` entry.

## Deployment

- **Two hosts:**
  - `plugins.rustgis.app` (GitHub Pages; the `CNAME` file, with DNS a CNAME to `opengeos.github.io`) serves the site, `registry.json`, `blocklist.json` and `packages.json`.
  - `packages.rustgis.app` serves the packages from the `rustgis-plugins` Cloudflare R2 bucket, at `<id>/<id>-<version>.rgplugin`. Packages never change once published, and every version stays available.
- **Incremental builds:** each push to `main` builds only what changed. `scripts/build.py` reads the previous deploy's `packages.json` and downloads only the packages whose source changed.
- **Upload before deploy:** `scripts/upload.py` puts the new packages in the bucket, checking each one back, before the registry is deployed. It needs the `CLOUDFLARE_R2_TOKEN` secret (a Cloudflare API token with *Workers R2 Storage: Edit*) and `CLOUDFLARE_ACCOUNT_ID`.
- **Previews:** each pull request's new packages are published to Cloudflare Pages (`pr-<N>.rustgis-plugins-preview.pages.dev`, the `rustgis-plugins-preview` project) by `plugin-preview.yml`, with an **Open in RustGIS** link that opens web.rustgis.app on the preview's registry (or paste the preview's `registry.json` into **Plugin Manager › Settings › Plugin registry**). The non-preview plugins are listed as published. It needs the `CLOUDFLARE_PAGES_TOKEN` secret (an API token with *Cloudflare Pages: Edit*).
- **CI:** pull requests check and run only the plugins they change. The **Nightly** workflow runs every published plugin in the latest RustGIS.

Code is MIT licensed. Each plugin states its own license in its `plugin.json`.
