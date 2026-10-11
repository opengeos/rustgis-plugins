//! Basemaps: a ribbon tab of public basemaps and overlays.
//!
//! Each basemap is a command. Choosing one adds it to the map as a web tile layer through
//! RustGIS's `map.addTiles` (which needs the `network` permission), replacing the web basemap
//! already there; `{"add": true}` stacks it instead. Overlays (labels, roads, trails) go on top
//! of whatever basemap is showing, and choosing one again takes it away.
//!
//! The catalog (`catalog.rs`) and `plugin.json`'s commands are generated from one table.

use rustgis_plugin::{Value, host, json};

mod catalog;

pub use catalog::CATALOG;

/// A basemap or overlay in the catalog.
#[derive(Clone, Copy, Debug)]
pub struct Basemap {
    /// The command id, also kept on the tile layer to recognise it later.
    pub id: &'static str,
    pub name: &'static str,
    /// `raster` (XYZ tiles), `vector` (vector tiles or TileJSON) or `style` (a MapLibre style).
    pub kind: &'static str,
    pub url: &'static str,
    pub attribution: &'static str,
    pub max_zoom: u8,
    /// A transparent layer that stacks over the basemap.
    pub overlay: bool,
    /// What `{s}` in the address takes, one letter each.
    pub subdomains: &'static str,
}

fn handle(req: rustgis_plugin::Request) -> Result<Value, String> {
    if let Some(b) = CATALOG.iter().find(|b| b.id == req.id) {
        return choose(b, req.bool("add").unwrap_or(false));
    }
    match req.id.as_str() {
        "clear" => clear(false),
        "clear-overlays" => clear(true),
        "current" => current(),
        other => Err(format!("Basemaps has no command '{other}'")),
    }
}

rustgis_plugin::export!(handle);

/// The map's tile layers, top first: `[{"index", "name", "id", "overlay", …}]`.
fn tile_layers() -> Result<Vec<Value>, String> {
    host::run("map.tiles", json!({}))?.as_array().cloned().ok_or_else(|| "map.tiles returned an unexpected result".to_string())
}

/// The parameters of `map.addTiles` for a catalog entry.
pub fn add_params(b: &Basemap, replace: bool) -> Value {
    let mut p = json!({
        "url": b.url, "kind": b.kind, "name": b.name, "attribution": b.attribution, "maxZoom": b.max_zoom,
        "overlay": b.overlay, "id": b.id, "replace": replace,
    });
    if !b.subdomains.is_empty() {
        p["subdomains"] = json!(b.subdomains.chars().map(String::from).collect::<Vec<_>>());
    }
    p
}

fn choose(b: &Basemap, add: bool) -> Result<Value, String> {
    // An overlay already on the map is taken away again.
    if b.overlay && tile_layers()?.iter().any(|t| t["id"] == b.id) {
        host::run("map.removeTiles", json!({"id": b.id}))?;
        host::notify(&format!("Removed {}", b.name));
        return Ok(json!({"removed": b.name}));
    }
    let out = host::run("map.addTiles", add_params(b, !b.overlay && !add))?;
    host::notify(&format!("{} {}", if b.overlay { "Overlay:" } else { "Basemap:" }, b.name));
    Ok(out)
}

/// Removes the web basemaps (or the overlays), bottom first so the indexes stay valid.
fn clear(overlays: bool) -> Result<Value, String> {
    let mut removed = Vec::new();
    for t in tile_layers()?.iter().rev() {
        if t["overlay"].as_bool().unwrap_or(false) == overlays {
            host::run("map.removeTiles", json!({"index": t["index"]}))?;
            removed.push(t["name"].clone());
        }
    }
    let what = if overlays { "overlays" } else { "web basemaps" };
    host::notify(&if removed.is_empty() { format!("No {what} to remove") } else { format!("Removed {} {what}", removed.len()) });
    Ok(json!({"removed": removed}))
}

fn current() -> Result<Value, String> {
    let layers = tile_layers()?;
    let names: Vec<&str> = layers.iter().filter_map(|t| t["name"].as_str()).collect();
    host::notify(&if names.is_empty() { "No web tile layers: the built-in basemap is showing".to_string() } else { names.join(" over ") });
    Ok(Value::Array(layers))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustgis_plugin::serde_json;

    #[test]
    fn catalog_and_manifest_agree() {
        let manifest: Value = serde_json::from_str(include_str!("../plugin.json")).unwrap();
        let commands: Vec<&str> = manifest["commands"].as_array().unwrap().iter().filter_map(|c| c["id"].as_str()).collect();
        for b in CATALOG {
            assert!(commands.contains(&b.id), "{} has no command", b.id);
            assert!(b.url.starts_with("https://"), "{}: {}", b.id, b.url);
            assert!(matches!(b.kind, "raster" | "vector" | "style"), "{}", b.id);
            if b.kind == "raster" {
                assert!(b.url.contains("{z}") && b.url.contains("{x}") && b.url.contains("{y}"), "{}", b.id);
                assert!(!b.attribution.is_empty(), "{} needs a credit", b.id);
            }
            assert_eq!(b.url.contains("{s}"), !b.subdomains.is_empty(), "{}", b.id);
        }
        for c in commands {
            assert!(CATALOG.iter().any(|b| b.id == c) || ["clear", "clear-overlays", "current"].contains(&c), "{c} isn't handled");
        }
        assert_eq!(manifest["permissions"], json!(["edit", "network"]));
    }

    #[test]
    fn builds_add_tiles_params() {
        let topo = CATALOG.iter().find(|b| b.id == "opentopomap").unwrap();
        let p = add_params(topo, true);
        assert_eq!(p["subdomains"], json!(["a", "b", "c"]));
        assert_eq!(p["replace"], true);
        let labels = CATALOG.iter().find(|b| b.id == "esri-places").unwrap();
        assert_eq!(add_params(labels, false)["overlay"], true);
    }
}
