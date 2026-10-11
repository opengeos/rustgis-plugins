//! Map Explorer: a plugin with a ribbon tab of its own ("Explorer").
//!
//! The tab is declared in `plugin.json` (`"tab"`): groups of the plugin's commands, a dropdown
//! menu of commands (Continent), and a tool (`tool:field-summary`, which opens in the
//! Geoprocessing pane). This file only answers the commands and the tool.
//!
//! The sandbox has no clock or random source, so "random" picks are seeded from the view: each
//! jump moves the view, so the next pick differs.

use rustgis_plugin::{Kind, Map, Request, ToolOutput, Value, geojson, host, json};

/// Continents: (command, lon, lat, scale).
const CONTINENTS: &[(&str, f64, f64, f64)] = &[
    ("go-africa", 20.0, 2.0, 40_000_000.0),
    ("go-asia", 90.0, 35.0, 50_000_000.0),
    ("go-europe", 15.0, 52.0, 25_000_000.0),
    ("go-north-america", -100.0, 45.0, 45_000_000.0),
    ("go-south-america", -60.0, -18.0, 40_000_000.0),
    ("go-oceania", 140.0, -25.0, 40_000_000.0),
];

fn handle(req: Request) -> Result<Value, String> {
    if req.kind == Kind::Tool {
        return match req.id.as_str() {
            "field-summary" => field_summary(&req),
            other => Err(format!("Map Explorer has no tool '{other}'")),
        };
    }
    if let Some(&(_, lon, lat, scale)) = CONTINENTS.iter().find(|c| c.0 == req.id) {
        return host::run("map.goToXY", json!({"lon": lon, "lat": lat, "scale": scale}));
    }
    match req.id.as_str() {
        "random-place" => random_place(),
        "select-largest" => select_largest(req.f64("count").unwrap_or(10.0)),
        "select-sample" => select_sample(req.f64("fraction").unwrap_or(0.1)),
        "layer-report" => layer_report(),
        other => Err(format!("Map Explorer has no command '{other}'")),
    }
}

rustgis_plugin::export!(handle);

/// The selected (active) layer in the Contents pane: its description from `host.layers`.
fn active_layer() -> Result<Value, String> {
    let layers = host::layers()?;
    layers
        .as_array()
        .and_then(|a| a.iter().find(|l| l["active"] == true).cloned())
        .ok_or_else(|| "select a layer in the Contents pane first".to_string())
}

/// The active feature layer's name and features.
fn active_features() -> Result<(String, Vec<Value>), String> {
    let layer = active_layer()?;
    if layer["raster"] == true {
        return Err("this works on feature layers, not rasters".into());
    }
    let name = layer["name"].as_str().unwrap_or_default().to_string();
    let fc = host::features(&name, false)?;
    let features = fc["features"].as_array().cloned().unwrap_or_default();
    if features.is_empty() {
        return Err(format!("{name} has no features"));
    }
    Ok((name, features))
}

/// A seed from the view, which changes as the user moves around.
fn seed() -> u64 {
    let v = host::view().unwrap_or(Value::Null);
    let (x, y, s) = (v["center"][0].as_f64().unwrap_or(0.0), v["center"][1].as_f64().unwrap_or(0.0), v["scale"].as_f64().unwrap_or(1.0));
    x.to_bits() ^ y.to_bits().rotate_left(21) ^ s.to_bits().rotate_left(42) ^ 0x9E37_79B9_7F4A_7C15
}

/// SplitMix64.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }
}

fn random_place() -> Result<Value, String> {
    let (name, features) = active_features()?;
    let mut rng = Rng(seed());
    let f = &features[rng.below(features.len())];
    let pts = geojson::positions(&f["geometry"]);
    let b = geojson::bbox(&pts).ok_or("that feature has no geometry")?;
    let (lon, lat) = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0);
    // Close for a point, enough to see the whole feature otherwise.
    let span_km = ((b[2] - b[0]).abs() * 111.0 * lat.to_radians().cos().abs()).max((b[3] - b[1]).abs() * 111.0);
    let scale = (span_km * 1000.0 / 0.25 * 1.5).clamp(250_000.0, 60_000_000.0);
    host::run("map.goToXY", json!({"lon": lon, "lat": lat, "scale": scale}))?;
    let label = f["properties"].as_object().and_then(|p| p.values().find_map(Value::as_str)).unwrap_or("a feature");
    host::notify(&format!("{label} ({name})"));
    Ok(json!({"layer": name, "row": f["row"], "lon": lon, "lat": lat}))
}

/// A feature's size: its area (polygons) or length (lines), in rough square or plain km.
fn size(g: &Value) -> f64 {
    let km = |a: [f64; 2], b: [f64; 2]| {
        let k = ((a[1] + b[1]) / 2.0).to_radians().cos().abs();
        (((b[0] - a[0]) * 111.32 * k).powi(2) + ((b[1] - a[1]) * 110.57).powi(2)).sqrt()
    };
    let ring_area = |r: &[Value]| {
        let pts: Vec<[f64; 2]> = r.iter().filter_map(|p| Some([p[0].as_f64()?, p[1].as_f64()?])).collect();
        let lat = pts.iter().map(|p| p[1]).sum::<f64>() / pts.len().max(1) as f64;
        let k = lat.to_radians().cos().abs() * 111.32 * 110.57;
        pts.windows(2).map(|w| w[0][0] * w[1][1] - w[1][0] * w[0][1]).sum::<f64>().abs() / 2.0 * k
    };
    let polygon = |p: &Value| -> f64 {
        let rings = p.as_array().cloned().unwrap_or_default();
        let outer = rings.first().and_then(Value::as_array).map(|r| ring_area(r)).unwrap_or(0.0);
        outer - rings.iter().skip(1).filter_map(Value::as_array).map(|r| ring_area(r)).sum::<f64>()
    };
    let line = |l: &Value| -> f64 {
        let pts: Vec<[f64; 2]> =
            l.as_array().map(|a| a.iter().filter_map(|p| Some([p[0].as_f64()?, p[1].as_f64()?])).collect()).unwrap_or_default();
        pts.windows(2).map(|w| km(w[0], w[1])).sum()
    };
    match g["type"].as_str() {
        Some("Polygon") => polygon(&g["coordinates"]),
        Some("MultiPolygon") => g["coordinates"].as_array().map(|ps| ps.iter().map(polygon).sum()).unwrap_or(0.0),
        Some("LineString") => line(&g["coordinates"]),
        Some("MultiLineString") => g["coordinates"].as_array().map(|ls| ls.iter().map(line).sum()).unwrap_or(0.0),
        Some("GeometryCollection") => g["geometries"].as_array().map(|gs| gs.iter().map(size).sum()).unwrap_or(0.0),
        _ => 0.0,
    }
}

fn select(name: &str, rows: &[u64]) -> Result<(), String> {
    host::run("select.rows", json!({"layer": name, "rows": rows}))?;
    host::run("select.zoomTo", json!({}))?;
    Ok(())
}

fn select_largest(count: f64) -> Result<Value, String> {
    let (name, features) = active_features()?;
    let mut sized: Vec<(f64, u64)> = features.iter().map(|f| (size(&f["geometry"]), f["row"].as_u64().unwrap_or(0))).collect();
    if sized.iter().all(|(s, _)| *s == 0.0) {
        return Err(format!("{name}'s features have no area or length (points?)"));
    }
    sized.sort_by(|a, b| b.0.total_cmp(&a.0));
    let n = (count.max(1.0) as usize).min(sized.len());
    let rows: Vec<u64> = sized.iter().take(n).map(|(_, r)| *r).collect();
    select(&name, &rows)?;
    host::notify(&format!("Selected the {n} largest features of {name}."));
    Ok(json!({"layer": name, "selected": n}))
}

fn select_sample(fraction: f64) -> Result<Value, String> {
    let (name, features) = active_features()?;
    let n = ((features.len() as f64 * fraction.clamp(0.0, 1.0)).round() as usize).clamp(1, features.len());
    // A partial Fisher-Yates shuffle of the rows.
    let mut rows: Vec<u64> = features.iter().map(|f| f["row"].as_u64().unwrap_or(0)).collect();
    let mut rng = Rng(seed());
    for i in 0..n {
        let j = i + rng.below(rows.len() - i);
        rows.swap(i, j);
    }
    rows.truncate(n);
    rows.sort_unstable();
    select(&name, &rows)?;
    host::notify(&format!("Selected {n} of {} features of {name} at random.", features.len()));
    Ok(json!({"layer": name, "selected": n}))
}

fn layer_report() -> Result<Value, String> {
    let l = active_layer()?;
    let name = l["name"].as_str().unwrap_or_default();
    let text = if l["raster"] == true {
        format!("{name}: a raster layer.")
    } else {
        let fields = l["fields"].as_array().map(Vec::len).unwrap_or(0);
        format!(
            "{name}: {} {} features, {fields} fields, {} selected.",
            l["features"],
            l["geometry"].as_str().unwrap_or("").to_lowercase(),
            l["selected"]
        )
    };
    host::notify(&text);
    Ok(l)
}

fn field_summary(req: &Request) -> Result<Value, String> {
    let name = req.str("input").unwrap_or("Input").to_string();
    let features = req.features("input")?;
    // Numeric fields, in the order they first appear.
    let mut order: Vec<String> = Vec::new();
    let mut stats: Map<String, Value> = Map::new();
    for f in features {
        let Some(props) = f["properties"].as_object() else { continue };
        for (k, v) in props {
            let Some(x) = v.as_f64().filter(|x| x.is_finite()) else { continue };
            if !stats.contains_key(k) {
                order.push(k.clone());
                stats.insert(k.clone(), json!([0u64, x, x, 0.0]));
            }
            if let Some(s) = stats.get_mut(k).and_then(Value::as_array_mut) {
                let n = s[0].as_u64().unwrap_or(0) + 1;
                let (lo, hi, sum) =
                    (s[1].as_f64().unwrap_or(x).min(x), s[2].as_f64().unwrap_or(x).max(x), s[3].as_f64().unwrap_or(0.0) + x);
                *s = vec![json!(n), json!(lo), json!(hi), json!(sum)];
            }
        }
    }
    if order.is_empty() {
        return Err(format!("{name} has no numeric fields"));
    }
    let rows: Vec<Value> = order
        .iter()
        .filter_map(|k| {
            let s = stats.get(k)?.as_array()?;
            let (n, sum) = (s[0].as_f64()?, s[3].as_f64()?);
            Some(json!({"type": "Feature", "geometry": null, "properties": {
                "field": k, "count": n, "min": s[1], "max": s[2], "mean": sum / n.max(1.0), "sum": sum,
            }}))
        })
        .collect();
    let n = rows.len();
    Ok(ToolOutput::new()
        .table(&format!("{name} field summary"), geojson::collection(rows))
        .message(format!("{n} numeric fields of {} features", features.len()))
        .into())
}
