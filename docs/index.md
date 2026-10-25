# RustGIS Plugins

Plugins add commands and geoprocessing tools to [RustGIS](https://rustgis.app). Most plugins are
WebAssembly modules that run in a sandbox, and the same package works in the desktop app, on
the command line and in the [web app](https://web.rustgis.app). A process plugin, such as a native
Rust executable or Python script, is a program instead: it isn't sandboxed and runs on the
desktop and the command line only (see [Safety](#safety)).

- **[Catalog](catalog.md)**: every plugin in the registry.
- **[Write a plugin](develop.md)**: the SDK, the manifest and what a plugin can do.
- **[Publish a plugin](registry.md)**: add yours to the registry.

## Installing plugins

In RustGIS, open **Plugins › Plugin Manager › Browse** and choose **Install**. RustGIS downloads
the package, checks it against the SHA-256 published here, and shows what the plugin may do
before loading it. The plugin's commands appear on the **Plugins** tab, and its tools appear in
the **Geoprocessing** pane under **Plugins ›**.

On the command line:

```sh
rustgis-cli plugins browse
rustgis-cli plugins get vector-toolkit
rustgis-cli run --sample --cmd 'gp.run={"tool":"plugin:vector-toolkit/hexagon-grid","params":{"input":"Countries","size":300}}'
```

## Safety

A plugin can read the map. It can change the project only with the `edit` permission, use files
only with the `files` permission, and have RustGIS fetch from addresses it chooses (web tile
layers, data files, web services) only with the `network` permission. You see them before installing.
Plugins have no other access to your computer or the network. Each call has a time and memory budget, so a misbehaving
plugin is stopped, and it can't crash RustGIS. The exception is a process plugin (a native Rust
executable or Python script, for example), marked **Not sandboxed**: it runs as a program on
your computer and can do anything you can, so it is reviewed more strictly, and it runs only on
the desktop and the command line. Every plugin here is reviewed before it is listed.
Withdrawn plugins are listed in [`blocklist.json`](https://plugins.rustgis.app/blocklist.json),
and RustGIS turns them off.

## For RustGIS

The registry is [`registry.json`](https://plugins.rustgis.app/registry.json); packages are under
`packages/<id>/<id>-<version>.rgplugin` and never change once published.
