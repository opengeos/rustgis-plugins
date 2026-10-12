//! Hello RustGIS: a starter plugin. Copy this folder to begin your own.
//!
//! - `layer-report` reads the map (`host::layers`) and shows a summary (`host::notify`).
//! - `zoom-largest` runs a RustGIS command (`layer.zoomTo`) like a ribbon button would.
//! - `pin-center` adds a layer (`host::add_layer`), which needs the `edit` permission.
//! - `bounding-boxes` is a geoprocessing tool: it gets its input layer as GeoJSON and returns
//!   a new layer.
//!
//! Messages are worded in the interface language with `Request::tr`, from the `i18n` table in
//! `plugin.json` (layer names are data, so they stay in English).

use rustgis_plugin::{Kind, Request, ToolOutput, Value, geojson, host, json};

/// `plugin.json`, for its translations.
const MANIFEST: &str = include_str!("../plugin.json");

fn handle(req: Request) -> Result<Value, String> {
    match (req.kind, req.id.as_str()) {
        (Kind::Command, "layer-report") => layer_report(&req),
        (Kind::Command, "zoom-largest") => zoom_largest(&req),
        (Kind::Command, "pin-center") => pin_center(&req),
        (Kind::Tool, "bounding-boxes") => bounding_boxes(&req),
        (_, other) => Err(format!("Hello RustGIS has no '{other}'")),
    }
}

rustgis_plugin::export!(handle);

/// The vector layers: (name, feature count).
fn vector_layers() -> Result<Vec<(String, u64)>, String> {
    let layers = host::layers()?;
    Ok(layers
        .as_array()
        .map(|a| {
            a.iter()
                .filter(|l| l["raster"] != true)
                .map(|l| (l["name"].as_str().unwrap_or_default().to_string(), l["features"].as_u64().unwrap_or(0)))
                .collect()
        })
        .unwrap_or_default())
}

fn layer_report(req: &Request) -> Result<Value, String> {
    let layers = host::layers()?;
    let all = layers.as_array().cloned().unwrap_or_default();
    let rasters = all.iter().filter(|l| l["raster"] == true).count();
    let features: u64 = all.iter().filter_map(|l| l["features"].as_u64()).sum();
    let (n, r, f) = (all.len().to_string(), rasters.to_string(), features.to_string());
    let text =
        req.tr(MANIFEST, "{n} layers ({rasters} raster) with {features} features in all.", &[("n", &n), ("rasters", &r), ("features", &f)]);
    host::notify(&text);
    Ok(json!({"layers": all.len(), "rasters": rasters, "features": features}))
}

fn zoom_largest(req: &Request) -> Result<Value, String> {
    let (name, n) =
        vector_layers()?.into_iter().max_by_key(|(_, n)| *n).ok_or_else(|| req.tr(MANIFEST, "The map has no feature layers.", &[]))?;
    host::run("layer.zoomTo", json!({"layer": name}))?;
    host::notify(&req.tr(MANIFEST, "Zoomed to {name} ({n} features).", &[("name", &name), ("n", &n.to_string())]));
    Ok(json!({"layer": name, "features": n}))
}

fn pin_center(req: &Request) -> Result<Value, String> {
    let view = host::view()?;
    let (lon, lat) = (view["center"][0].as_f64().unwrap_or(0.0), view["center"][1].as_f64().unwrap_or(0.0));
    let name = req.str("name").unwrap_or("Pins");
    let fc = geojson::collection(vec![geojson::feature(geojson::point(lon, lat), json!({"lon": lon, "lat": lat}))]);
    host::add_layer(name, fc)
}

fn bounding_boxes(req: &Request) -> Result<Value, String> {
    let features = req.features("input")?;
    let name = req.str("input").unwrap_or("Input");
    let boxes: Vec<Value> = if req.bool("whole") == Some(true) {
        let all: Vec<[f64; 2]> = features.iter().flat_map(|f| geojson::positions(&f["geometry"])).collect();
        geojson::bbox(&all).map(|b| vec![box_feature(b, json!({"features": features.len()}))]).unwrap_or_default()
    } else {
        features
            .iter()
            .filter_map(|f| geojson::bbox(&geojson::positions(&f["geometry"])).map(|b| box_feature(b, f["properties"].clone())))
            .collect()
    };
    let n = boxes.len();
    Ok(ToolOutput::new()
        .layer(&format!("{name} boxes"), geojson::collection(boxes))
        .message(req.tr(MANIFEST, "{n} bounding boxes", &[("n", &n.to_string())]))
        .into())
}

fn box_feature(b: [f64; 4], properties: Value) -> Value {
    geojson::feature(geojson::polygon(&[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]), properties)
}
