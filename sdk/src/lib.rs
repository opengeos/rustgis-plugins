//! The RustGIS plugin SDK: write a RustGIS plugin in Rust and build it as a WebAssembly
//! module (`wasm32-unknown-unknown`).
//!
//! A plugin is a function that answers requests. RustGIS calls it when the user runs one of
//! the plugin's commands or tools (declared in `plugin.json`); the plugin reads the map and
//! changes it through [`host`], and returns JSON.
//!
//! ```ignore
//! use rustgis_plugin::{Request, json, host};
//!
//! fn handle(req: Request) -> Result<rustgis_plugin::Value, String> {
//!     match req.id.as_str() {
//!         "hello" => {
//!             let n = host::layers()?.as_array().map_or(0, Vec::len);
//!             host::notify(&format!("Hello! The map has {n} layers."));
//!             Ok(json!({"layers": n}))
//!         }
//!         other => Err(format!("unknown command '{other}'")),
//!     }
//! }
//!
//! rustgis_plugin::export!(handle);
//! ```
//!
//! The ABI underneath (JSON over linear memory, ABI version [`ABI`]) is described in RustGIS's
//! `docs/plugins.md`; this crate hides it.
pub use serde_json::{self, Map, Value, json};

/// `s` from `i18n` (translations by language code): the exact code first, then the language
/// without its region, then any region of it (`zh` finds `zh-CN`), as RustGIS looks them up.
fn translation<'a>(i18n: &'a Map<String, Value>, lang: &str, s: &str) -> Option<&'a str> {
    let base_of = |l: &str| l.split(['-', '_']).next().unwrap_or(l).to_ascii_lowercase();
    let base = base_of(lang);
    let pick = |want: &dyn Fn(&str) -> bool| {
        i18n.iter().filter(|(k, _)| want(k)).find_map(|(_, t)| t.get(s).and_then(Value::as_str)).filter(|t| !t.is_empty())
    };
    pick(&|k| k.eq_ignore_ascii_case(lang)).or_else(|| pick(&|k| k.eq_ignore_ascii_case(&base))).or_else(|| pick(&|k| base_of(k) == base))
}

/// `template` with each `{name}` replaced by its value in `args`, in one pass (a value is never
/// read again as a template).
fn fill(template: &str, args: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        match after.find('}').and_then(|close| args.iter().find(|(k, _)| *k == &after[..close]).map(|(_, v)| (close, v))) {
            Some((close, v)) => {
                out.push_str(v);
                rest = &after[close + 1..];
            }
            None => {
                out.push('{');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// The plugin ABI this SDK speaks. `plugin.json`'s `abi` must match it.
pub const ABI: u32 = 1;

/// What RustGIS asks the plugin to do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// One of the plugin's commands (a button on the Plugins tab, command search, scripts).
    Command,
    /// One of the plugin's geoprocessing tools.
    Tool,
}

/// A request from RustGIS.
#[derive(Clone, Debug)]
pub struct Request {
    pub kind: Kind,
    /// The command or tool id, as declared in `plugin.json`.
    pub id: String,
    /// The parameters: a command's, or a tool's form values (defaults filled in).
    pub params: Value,
    /// For tools: each layer parameter's features as a GeoJSON FeatureCollection, by layer
    /// name (only the selected features when the layer has a selection).
    pub layers: Map<String, Value>,
    /// The interface language's code (`en`, `de`, `zh`…): word messages in it with [`Request::tr`].
    pub lang: String,
}

impl Request {
    fn parse(v: Value) -> Request {
        let kind = if v.get("kind").and_then(Value::as_str) == Some("tool") { Kind::Tool } else { Kind::Command };
        Request {
            kind,
            id: v.get("id").and_then(Value::as_str).unwrap_or_default().to_string(),
            params: v.get("params").cloned().unwrap_or(Value::Null),
            layers: v.get("layers").and_then(Value::as_object).cloned().unwrap_or_default(),
            // RustGIS older than the `lang` field speaks English.
            lang: v.get("lang").and_then(Value::as_str).unwrap_or("en").to_string(),
        }
    }

    /// `s` in the interface language, from the `i18n` table of the plugin's `plugin.json`
    /// (`manifest` is its text: `include_str!("../plugin.json")`), with each `{name}` placeholder
    /// filled from `args`. A string with no translation stays in English.
    ///
    /// ```ignore
    /// const MANIFEST: &str = include_str!("../plugin.json");
    /// host::notify(&req.tr(MANIFEST, "Zoomed to {name}.", &[("name", &layer)]));
    /// ```
    pub fn tr(&self, manifest: &str, s: &str, args: &[(&str, &str)]) -> String {
        let manifest: Value = serde_json::from_str(manifest).unwrap_or(Value::Null);
        let text = manifest.get("i18n").and_then(Value::as_object).and_then(|t| translation(t, &self.lang, s)).unwrap_or(s);
        fill(text, args)
    }

    /// A text parameter.
    pub fn str(&self, name: &str) -> Option<&str> {
        self.params.get(name).and_then(Value::as_str)
    }

    /// A number parameter (forms may send numbers as text).
    pub fn f64(&self, name: &str) -> Option<f64> {
        let v = self.params.get(name)?;
        v.as_f64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok())).filter(|x: &f64| x.is_finite())
    }

    /// A switch parameter.
    pub fn bool(&self, name: &str) -> Option<bool> {
        let v = self.params.get(name)?;
        v.as_bool().or_else(|| v.as_str().map(|s| matches!(s, "true" | "yes" | "1" | "on")))
    }

    /// The features of the layer a tool parameter names, as a GeoJSON FeatureCollection.
    pub fn layer(&self, param: &str) -> Result<&Value, String> {
        let name = self.str(param).ok_or_else(|| format!("'{param}' is required"))?;
        self.layers.get(name).ok_or_else(|| format!("no layer named '{name}'"))
    }

    /// The features of the layer a tool parameter names.
    pub fn features(&self, param: &str) -> Result<&[Value], String> {
        Ok(self.layer(param)?.get("features").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[]))
    }
}

/// What a tool returns: new layers and tables, or a selection, and messages for the
/// Geoprocessing pane's history.
#[derive(Clone, Debug, Default)]
pub struct ToolOutput {
    layers: Vec<Value>,
    tables: Vec<Value>,
    selection: Option<Value>,
    messages: Vec<String>,
}

impl ToolOutput {
    pub fn new() -> ToolOutput {
        ToolOutput::default()
    }

    /// Adds a layer named `name` from a GeoJSON FeatureCollection.
    pub fn layer(mut self, name: &str, feature_collection: Value) -> ToolOutput {
        self.layers.push(json!({"name": name, "geojson": feature_collection}));
        self
    }

    /// Adds a standalone table (a FeatureCollection whose features have no geometry).
    pub fn table(mut self, name: &str, feature_collection: Value) -> ToolOutput {
        self.tables.push(json!({"name": name, "geojson": feature_collection}));
        self
    }

    /// Selects `rows` (feature indexes) of the layer `layer` instead of adding layers.
    pub fn select(mut self, layer: &str, rows: &[usize]) -> ToolOutput {
        self.selection = Some(json!({"layer": layer, "rows": rows}));
        self
    }

    /// A line for the run's messages.
    pub fn message(mut self, text: impl Into<String>) -> ToolOutput {
        self.messages.push(text.into());
        self
    }
}

impl From<ToolOutput> for Value {
    fn from(o: ToolOutput) -> Value {
        let mut m = Map::new();
        if let Some(s) = o.selection {
            m.insert("selection".into(), s);
        }
        m.insert("layers".into(), Value::Array(o.layers));
        m.insert("tables".into(), Value::Array(o.tables));
        m.insert("messages".into(), o.messages.into_iter().map(Value::String).collect());
        Value::Object(m)
    }
}

/// Helpers for building GeoJSON.
pub mod geojson {
    use super::*;

    /// A FeatureCollection of `features`.
    pub fn collection(features: Vec<Value>) -> Value {
        json!({"type": "FeatureCollection", "features": features})
    }

    /// A Feature with a geometry and properties.
    pub fn feature(geometry: Value, properties: Value) -> Value {
        json!({"type": "Feature", "geometry": geometry, "properties": properties})
    }

    pub fn point(x: f64, y: f64) -> Value {
        json!({"type": "Point", "coordinates": [x, y]})
    }

    /// A polygon from one closed ring of `[x, y]` positions (closed for you if needed).
    pub fn polygon(ring: &[[f64; 2]]) -> Value {
        let mut r: Vec<[f64; 2]> = ring.to_vec();
        if let (Some(a), Some(b)) = (r.first().copied(), r.last().copied())
            && a != b
        {
            r.push(a);
        }
        json!({"type": "Polygon", "coordinates": [r]})
    }

    /// Every position in a GeoJSON geometry (any type, nested collections included).
    pub fn positions(geometry: &Value) -> Vec<[f64; 2]> {
        fn walk(v: &Value, out: &mut Vec<[f64; 2]>) {
            match v {
                Value::Array(a) if a.len() >= 2 && a.iter().take(2).all(Value::is_number) => {
                    if let (Some(x), Some(y)) = (a[0].as_f64(), a[1].as_f64()) {
                        out.push([x, y]);
                    }
                }
                Value::Array(a) => a.iter().for_each(|x| walk(x, out)),
                _ => {}
            }
        }
        let mut out = Vec::new();
        if let Some(gs) = geometry.get("geometries").and_then(Value::as_array) {
            for g in gs {
                out.extend(positions(g));
            }
        } else if let Some(c) = geometry.get("coordinates") {
            walk(c, &mut out);
        }
        out
    }

    /// The bounding box `[min_x, min_y, max_x, max_y]` of positions, if there are any.
    pub fn bbox(points: &[[f64; 2]]) -> Option<[f64; 4]> {
        let first = points.first()?;
        Some(
            points
                .iter()
                .fold([first[0], first[1], first[0], first[1]], |b, p| [b[0].min(p[0]), b[1].min(p[1]), b[2].max(p[0]), b[3].max(p[1])]),
        )
    }
}

/// Talking to RustGIS while a request runs.
pub mod host {
    use super::*;

    #[link(wasm_import_module = "rustgis")]
    unsafe extern "C" {
        #[link_name = "call"]
        fn host_call(ptr: *const u8, len: usize) -> i32;
        #[link_name = "result"]
        fn host_result(ptr: *mut u8);
        #[link_name = "log"]
        fn host_log(ptr: *const u8, len: usize);
    }

    /// Sends a request to RustGIS: a command id (`map.zoomIn`, `layer.symbology`…) or a
    /// `host.*` request, with its parameters.
    pub fn call(op: &str, params: Value) -> Result<Value, String> {
        let req = json!({"op": op, "params": params}).to_string();
        // SAFETY: the host reads `len` bytes at `ptr`, which `req` owns for the call.
        let n = unsafe { host_call(req.as_ptr(), req.len()) };
        let len = usize::try_from(n).map_err(|_| String::from("RustGIS didn't answer"))?;
        let mut buf: Vec<u8> = Vec::with_capacity(len);
        // SAFETY: the host writes exactly `len` bytes (the reply it measured) into `buf`'s
        // capacity, which is then marked initialized.
        unsafe {
            host_result(buf.as_mut_ptr());
            buf.set_len(len);
        }
        let reply: Value = serde_json::from_slice(&buf).map_err(|e| format!("unreadable reply: {e}"))?;
        match (reply.get("ok"), reply.get("error")) {
            (_, Some(e)) => Err(e.as_str().map(str::to_string).unwrap_or_else(|| e.to_string())),
            (Some(v), None) => Ok(v.clone()),
            _ => Ok(Value::Null),
        }
    }

    /// Runs a RustGIS command by id (it must be allowed by the plugin's permissions).
    pub fn run(command: &str, params: Value) -> Result<Value, String> {
        call(command, params)
    }

    /// A line in RustGIS's log (and, for tools, the run's messages).
    pub fn log(text: &str) {
        // SAFETY: the host reads `len` bytes at `ptr`, which `text` owns for the call.
        unsafe { host_log(text.as_ptr(), text.len()) }
    }

    /// The map's layers: `[{"id", "name", "raster", "geometry", "features", "selected",
    /// "active", "fields": [{"name", "type"}]}]`.
    pub fn layers() -> Result<Value, String> {
        call("host.layers", json!({}))
    }

    /// A layer's features as a GeoJSON FeatureCollection (each feature has its `row`).
    pub fn features(layer: &str, selected_only: bool) -> Result<Value, String> {
        call("host.features", json!({"layer": layer, "selected": selected_only}))
    }

    /// The view: `{"center": [lon, lat], "scale", "projection"}`.
    pub fn view() -> Result<Value, String> {
        call("host.view", json!({}))
    }

    /// A message in RustGIS's status bar.
    pub fn notify(text: &str) {
        let _ = call("host.notify", json!({"text": text}));
    }

    /// Adds a layer from a GeoJSON FeatureCollection (needs the `edit` permission).
    pub fn add_layer(name: &str, feature_collection: Value) -> Result<Value, String> {
        call("host.addLayer", json!({"name": name, "geojson": feature_collection}))
    }
}

/// Glue used by [`export!`]; not for direct use.
#[doc(hidden)]
pub mod abi {
    use super::*;

    pub fn alloc(len: i32) -> i32 {
        let mut v: Vec<u8> = Vec::with_capacity(usize::try_from(len).unwrap_or(0));
        let p = v.as_mut_ptr();
        core::mem::forget(v);
        p as i32
    }

    pub fn call(ptr: i32, len: i32, handler: fn(Request) -> Result<Value, String>) -> i64 {
        let len = usize::try_from(len).unwrap_or(0);
        // SAFETY: RustGIS wrote `len` bytes at `ptr`, a buffer `alloc` made with that capacity.
        let bytes = unsafe { Vec::from_raw_parts(ptr as *mut u8, len, len) };
        let reply = match serde_json::from_slice::<Value>(&bytes) {
            Ok(v) => match handler(Request::parse(v)) {
                Ok(v) => json!({"ok": v}),
                Err(e) => json!({"error": e}),
            },
            Err(e) => json!({"error": format!("unreadable request: {e}")}),
        };
        // Each call gets a fresh instance, so the reply is simply left in memory.
        let out: Box<[u8]> = reply.to_string().into_bytes().into_boxed_slice();
        let n = out.len() as i64;
        let p = Box::into_raw(out) as *mut u8 as i64;
        (p << 32) | n
    }
}

/// Exports `handler` (`fn(Request) -> Result<Value, String>`) as the plugin's entry point.
#[macro_export]
macro_rules! export {
    ($handler:path) => {
        #[unsafe(no_mangle)]
        pub extern "C" fn rustgis_alloc(len: i32) -> i32 {
            $crate::abi::alloc(len)
        }

        #[unsafe(no_mangle)]
        pub extern "C" fn rustgis_call(ptr: i32, len: i32) -> i64 {
            $crate::abi::call(ptr, len, $handler)
        }
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    const MANIFEST: &str =
        r#"{"i18n": {"de": {"Zoomed to {name}.": "Auf {name} gezoomt."}, "zh-CN": {"Zoomed to {name}.": "已缩放至 {name}。"}}}"#;

    fn request(lang: Option<&str>) -> Request {
        let mut v = json!({"abi": ABI, "kind": "command", "id": "z", "params": {}});
        if let Some(l) = lang {
            v["lang"] = json!(l);
        }
        Request::parse(v)
    }

    #[test]
    fn messages_follow_the_interface_language() {
        assert_eq!(request(None).lang, "en");
        assert_eq!(request(Some("de")).tr(MANIFEST, "Zoomed to {name}.", &[("name", "Cities")]), "Auf Cities gezoomt.");
        assert_eq!(request(Some("zh")).tr(MANIFEST, "Zoomed to {name}.", &[("name", "Cities")]), "已缩放至 Cities。");
        assert_eq!(request(Some("fr")).tr(MANIFEST, "Zoomed to {name}.", &[("name", "{name}")]), "Zoomed to {name}.");
        assert_eq!(request(Some("de")).tr("not json", "Hi {x", &[("x", "y")]), "Hi {x");
    }
}
