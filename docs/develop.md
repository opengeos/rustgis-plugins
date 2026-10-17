# Write a plugin

Copy [`plugins/hello`](https://github.com/opengeos/rustgis-plugins/tree/main/plugins/hello)
to start. Plugins are written in Rust with the [`rustgis-plugin`](https://github.com/opengeos/rustgis-plugins/tree/main/sdk)
SDK, and built for `wasm32-unknown-unknown` (`rustup target add wasm32-unknown-unknown`).

```toml
[lib]
crate-type = ["cdylib"]

[dependencies]
rustgis-plugin = { git = "https://github.com/opengeos/rustgis-plugins" }

[profile.release]
opt-level = "s"
lto = true
panic = "abort"
strip = true
```

```rust
use rustgis_plugin::{Kind, Request, ToolOutput, Value, geojson, host, json};

fn handle(req: Request) -> Result<Value, String> {
    match (req.kind, req.id.as_str()) {
        (Kind::Command, "layer-report") => {
            let n = host::layers()?.as_array().map_or(0, Vec::len);
            host::notify(&format!("{n} layers"));
            Ok(json!({"layers": n}))
        }
        (Kind::Tool, "bounding-boxes") => {
            let boxes: Vec<Value> = req.features("input")?.iter().filter_map(|f| {
                let b = geojson::bbox(&geojson::positions(&f["geometry"]))?;
                Some(geojson::feature(geojson::polygon(&[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]), json!({})))
            }).collect();
            Ok(ToolOutput::new().layer("Boxes", geojson::collection(boxes)).into())
        }
        (_, other) => Err(format!("unknown '{other}'")),
    }
}

rustgis_plugin::export!(handle);
```

```sh
cargo build --release --target wasm32-unknown-unknown
# package plugin.json and the module (renamed plugin.wasm) as <id>-<version>.rgplugin:
mkdir -p pkg && cp plugin.json pkg/ && cp target/wasm32-unknown-unknown/release/my_plugin.wasm pkg/plugin.wasm
(cd pkg && zip -X ../my-plugin-0.1.0.rgplugin plugin.json plugin.wasm)
```

To try it while you work, load the folder instead of a package. In RustGIS, use
**Plugins › Plugin Manager › Settings › Add Plugin Folder…**; on the command line, use
`rustgis-cli run --plugin ./pkg --sample --cmd 'plugin:my-plugin/hello'`. Inside this repository,
`python scripts/build.py` (Python 3.11 or later) builds and packages every plugin into `dist/`, and
`python scripts/smoke.py` runs them in RustGIS.

## `plugin.json`

```json
{
  "id": "hello-rustgis",
  "name": "Hello RustGIS",
  "version": "0.1.0",
  "description": "What it does, in a sentence or two.",
  "author": "You",
  "homepage": "https://github.com/you/your-plugin",
  "license": "MIT",
  "entry": "plugin.wasm",
  "abi": 1,
  "minRustgisVersion": "0.1.0",
  "permissions": ["edit"],
  "commands": [
    { "id": "layer-report", "label": "Layer\nReport", "help": "Shown on hover.", "icon": "stats", "params": "{}" }
  ],
  "tools": [
    {
      "id": "bounding-boxes", "name": "Bounding Boxes", "summary": "One line.", "toolbox": "Hello",
      "params": [
        { "name": "input", "label": "Input features", "kind": "vectorLayer", "required": true },
        { "name": "whole", "label": "One box for the whole layer", "kind": "bool", "default": false }
      ]
    }
  ],
  "i18n": { "de": { "Layer\nReport": "Layer-\nbericht" } }
}
```

- `id`: lower-case letters, digits and hyphens. Commands and tools get ids
  `plugin:<id>/<command>`, which the ribbon, command search, scripts and `rustgis-cli` use.
- `version` and `minRustgisVersion` look like `1.2.3`. RustGIS refuses a plugin that needs a
  newer RustGIS.
- `permissions`: `edit`, `files` and `network` (see [Safety](index.md#safety)). Ask only for what
  you use. `network` lets the plugin have RustGIS fetch from addresses the plugin gives: web
  tile layers (`map.addTiles`), data files (`layer.addUrl`, see
  [Data from the web](#data-from-the-web)) and the commands that query web services. An address
  can carry data, so it is a permission of its own.
- `commands[].icon`: the name of one of RustGIS's own icons (`stats`, `zoom_full`, `bookmark`,
  `buffer`…), or a puzzle piece when it isn't one.
- Tool parameter `kind`s: `layer`, `vectorLayer`, `polygonLayer`, `pointLayer`, `field`,
  `numericField`, `number`, `text`, `choice` (with `choices`), `bool` and `file` (see
  [File parameters](#file-parameters)).
- Limits: at most 64 commands and 64 tools, 32 parameters per tool, and 1 to 64 `choices` per
  `choice` parameter. For more values than that, use a `text` parameter and check it in the
  plugin.
- `i18n` translates the plugin's own text into the interface language, by language code. See
  [Translations](#translations).

A package is a zip archive named `<id>-<version>.rgplugin`. It holds `plugin.json` and the module
at its root, or inside one top-level folder. Packages are at most 64 MB, and modules at most 48 MB.

## File parameters

A tool can read a file the user picks, such as a NetCDF model run or a GeoTIFF, with a `file`
parameter. It needs the `files` permission, and a RustGIS with file parameters (set
`minRustgisVersion` to the first release that has them):

```json
{ "name": "model", "label": "Model output", "kind": "file", "required": true, "extensions": ["nc", "nc4"] }
```

`extensions` filters the file picker: at most 16, each 1 to 16 letters or digits, without the
dot. Leave it out for any file. RustGIS reads the file, up to 64 MB, and the tool gets it with
`req.take_file` (which decodes it in place, without a second copy) or `req.file`:

```rust
// In `fn handle(mut req: Request)`:
let model = req.take_file("model")?; // model.name ("run.nc") and model.bytes
```

To read raster and vector formats from the bytes inside the module, use a pure-Rust library that
builds for `wasm32-unknown-unknown`, such as [terrio](https://github.com/opengeos/terrio)
(`terrio::memory::open_raster_bytes(model.bytes, &model.name)`). A 64 MB file, its copy in the
request and what you make of it all share the module's 512 MB of memory.

## Data from the web

The module itself has no network access, but a command can ask RustGIS to download a file and
add it as layers with `layer.addUrl`. It needs the `network` and `edit` permissions, and RustGIS
0.2.1 or later.

```rust
let url = format!("https://www.confini-amministrativi.it/api/v2/it/{date}/{level}.gpkg");
let out = host::run("layer.addUrl", json!({"url": url, "name": "Comuni", "zoom": true}))?;
```

- The address is `http://`, `https://` or `s3://`. The format comes from its extension, else
  from the contents: GeoJSON, CSV, GeoParquet, FlatGeobuf, GeoPackage, Shapefile or other data
  in a `.zip`, KML, KMZ, GPX, GeoTIFF, or a PMTiles archive. `layer` picks one layer of a zip,
  KML or GPX; the other parameters are those of `layer.addData` (`rustgis-cli commands` lists
  them).
- Cloud-optimised GeoTIFFs, PMTiles, and FlatGeobuf or GeoParquet files with a spatial index
  are read in place, by range, rather than downloaded whole.
- In the desktop app and on the command line, the answer names the layers added. In the web
  app, which downloads off the frame, it is `{"pending": true, "url": ...}` and the layer
  appears when the download lands. There the server must allow requests from other sites
  (CORS), as for **Add Data › From URL**.

`layer.addData` opens files, not addresses. [IDE Chile](https://github.com/opengeos/rustgis-plugins/tree/main/plugins/ide-chile)
adds layers this way.

## Translations

RustGIS shows a plugin in the interface language when its `plugin.json` has an `i18n` table: for
each language code, the plugin's English strings (exactly as written, `\n` included) and their
translations. A string with no translation shows in English, so translations are optional.

- They cover the name and description (in the Plugin Manager, before the plugin is installed
  too), command labels and help, tool names, summaries and toolboxes, parameter labels and
  `choice` values, and the tab, group and menu labels.
- RustGIS's interface languages are `de`, `es`, `fr`, `it`, `nl`, `pt`, `ru`, `tr`, `az`, `id`,
  `vi`, `zh`, `ja` and `ko`. A code with a region matches its language: `zh-CN` serves `zh`.
- Messages the plugin builds while it runs: every request carries the interface language
  (`req.lang`), and `req.tr` looks a message up in the same table and fills its `{placeholders}`:

  ```rust
  const MANIFEST: &str = include_str!("../plugin.json");

  host::notify(&req.tr(MANIFEST, "Zoomed to {name}.", &[("name", &layer)]));
  ```

  Add `"Zoomed to {name}."` to the `i18n` tables like any other string. Layer and field names
  are data: leave them in English.
- `python scripts/i18n.py audit -v` lists what each plugin is missing per language (CI runs it
  and fails on a translation that drops a `{placeholder}`). `missing -o todo.json` writes the
  gaps to fill in, and `insert todo.json` adds them to the manifests.

[Hello RustGIS](https://github.com/opengeos/rustgis-plugins/tree/main/plugins/hello) translates
its messages this way.

## A tab of its own

By default a plugin's commands and tools get a group on the **Plugins** tab. A plugin can have a
ribbon tab of its own instead. The tab sits at the end of the tab strip, and **Help › Ribbon**
can hide it like any other. Declare it in `plugin.json`:

```json
"tab": {
  "label": "Explorer",
  "groups": [
    {
      "label": "Go To",
      "items": [
        { "menu": "Continent", "icon": "zoom_world", "help": "Go to a continent.", "items": ["go-africa", "go-europe"] },
        "random-place"
      ]
    },
    { "label": "Inspect", "items": ["layer-report", "tool:field-summary"] }
  ]
}
```

- `items` lists the buttons. An item is a command id, `tool:<id>` (which opens that tool in the
  Geoprocessing pane), or a dropdown `menu` whose `items` are command ids or `tool:<id>`.
- A tab has 1 to 8 groups, a group has 1 to 16 items, and a menu has 1 to 32 entries. Every
  entry must name one of the plugin's own commands or tools.
- The label can't be the name of one of RustGIS's tabs (Map, Insert, Analysis, Plugins…). The
  `i18n` translations apply to the tab, group and menu labels too.

[Map Explorer](https://github.com/opengeos/rustgis-plugins/tree/main/plugins/map-explorer) is
an example.

## What a plugin can ask for

Inside a request, `host::call(op, params)` sends one of these to RustGIS:

| Request | Answer |
|---|---|
| `host.info` | RustGIS's version, the ABI, the plugin's id and permissions |
| `host.layers` | `[{id, name, raster, visible, geometry, features, selected, active, fields: [{name, type}]}]` |
| `host.features` `{layer, selected?, limit?}` | the layer's features as GeoJSON; each feature has its `row` |
| `host.view` | `{center: [lon, lat], scale, projection}` |
| `host.notify` `{text, error?}` | shows a message in the status bar |
| `host.addLayer` `{name, geojson}` | adds a layer (`edit`) |
| `layer.addUrl` `{url, name?, layer?, zoom?, ...}` | downloads a data file and adds its layers (`edit` and `network`, see [Data from the web](#data-from-the-web)) |
| `map.addTiles` `{url, kind?, name?, attribution?, minZoom?, maxZoom?, tileSize?, tms?, subdomains?, opacity?, overlay?, replace?, id?}` | adds a web tile layer: raster tiles, vector tiles or a MapLibre style (`edit` and `network`); `id` marks it so the plugin can find it again in `map.tiles` |
| `map.tiles` | the map's tile layers, top first: `[{index, name, id, kind, url, visible, opacity, overlay, attribution, minZoom, maxZoom}]` |
| `map.removeTiles` `{index? \| name? \| id?}`, `map.setTiles` `{index? \| name?, visible?, opacity?, move?, rename?}` | removes (every one when none is named) and changes tile layers (`edit`) |
| any command id (`layer.zoomTo`, `select.byAttributes`, `layer.symbology`…) | what the command returns, within the plugin's permissions |

`rustgis-cli commands` lists every command id and its parameters.

Tools work differently. A tool receives its layer parameters' features as GeoJSON (only the
selected features when a layer has a selection), and its `file` parameters' files. It returns new layers, standalone tables or a
selection, plus messages for the Geoprocessing history. RustGIS adds the results as it does for
built-in tools, with undo. Tools can log, but they make no host requests.

## The ABI (version 1)

The SDK implements this; it is described here for other languages.

The module is `wasm32` with no imports except these three, in module `rustgis`:

- `call(ptr: i32, len: i32) -> i32` runs the host request at `ptr`
  (`{"op": "...", "params": {...}}`). It returns the length of the reply, which is
  `{"ok": value}` or `{"error": "text"}`, or `-1` when there is none.
- `result(ptr: i32)` copies that reply to `ptr`. Allocate the length `call` returned first.
- `log(ptr: i32, len: i32)` logs a UTF-8 line.

The module exports:

- `memory`.
- `rustgis_alloc(len: i32) -> i32`, which returns room for `len` bytes. RustGIS writes the
  request there.
- `rustgis_call(ptr: i32, len: i32) -> i64`, which handles the request and returns
  `reply_ptr << 32 | reply_len`. The reply is `{"ok": value}` or `{"error": "text"}`.

Requests are `{"abi": 1, "kind": "command" | "tool", "id": "...", "params": {...}, "layers": {...}}`.
Tools also get `layers`, which maps each layer's name to its FeatureCollection, and, when they
have `file` parameters, `files`, which maps each one's name to `{"name", "data"}` with the file's
contents in base64. A tool's `ok`
value is `{"layers": [{"name", "geojson"}], "tables": [...], "selection": {"layer", "rows"},
"messages": [...]}`.

Each call gets a fresh instance, so a module keeps no state between calls and need not free
memory. The fuel budget is about 2×10⁹ instructions for a command and 4×10¹⁰ for a tool.
