//! Historical Imagery: Esri's World Imagery Wayback, every archived release of World Imagery
//! since February 2014, as a web tile basemap.
//!
//! A release is added through RustGIS's `map.addTiles` (which needs the `network` permission),
//! with the id `wayback-<release number>`, so the plugin finds it on the map again: **Older** and
//! **Newer** step from the release on top, and a new release replaces it unless `{"add": true}`.
//! **Imagery Info** asks the release's metadata service, through `layer.addUrl`, for the imagery
//! source at a place: its capture date, provider, sensor and resolution, with its footprint.
//!
//! Esri publishes a release about once a month. The module has no network of its own, so the
//! releases it knows are baked in (`releases.rs`, generated with `plugin.json`'s commands by
//! `generate.py`), and a command that may need a newer one (Latest, Newer at the end, a date
//! past the baked list) reads Esri's live list instead: Wayback's WMTS capabilities, through
//! RustGIS's `service.layers`. When that can't be read, the baked list stands in.

use rustgis_plugin::{Request, Value, host, json};

mod releases;

pub use releases::RELEASES;

/// `plugin.json`, for its translations.
const MANIFEST: &str = include_str!("../plugin.json");

/// The id prefix of the plugin's tile layers.
const ID: &str = "wayback-";

/// The credit, as World Imagery gives it (Vantor is Maxar renamed).
const ATTRIBUTION: &str = "Tiles © Esri — Source: Esri, Vantor, Earthstar Geographics, and the GIS User Community (World Imagery Wayback)";

/// Wayback's WMTS capabilities: every release, as a layer.
const CAPABILITIES: &str = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/WMTSCapabilities.xml";

/// A World Imagery Wayback release.
#[derive(Clone, Debug, PartialEq)]
pub struct Release {
    /// The release number, which names its tiles.
    pub num: u32,
    /// The release date, `YYYY-MM-DD`.
    pub date: String,
    /// The name of its metadata MapServer.
    pub metadata: String,
}

impl Release {
    /// The tile layer's id.
    pub fn id(&self) -> String {
        format!("{ID}{}", self.num)
    }

    /// The tile layer's name.
    pub fn name(&self) -> String {
        format!("Wayback {}", self.date)
    }

    /// The tile template.
    pub fn tiles(&self) -> String {
        format!(
            "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{}/{{z}}/{{y}}/{{x}}",
            self.num
        )
    }

    /// The address of the imagery source at a place as GeoJSON: the metadata layer for the
    /// zoom level (layer 0 is zoom 23, layer 13 zoom 10 and below).
    pub fn metadata_url(&self, lon: f64, lat: f64, zoom: f64) -> String {
        let layer = (23.0 - zoom.round()).clamp(0.0, 13.0);
        format!(
            "https://metadata.maptiles.arcgis.com/arcgis/rest/services/{}/MapServer/{layer}/query?where=1%3D1&geometry={lon:.6},{lat:.6}&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=SRC_DATE2,NICE_DESC,SRC_DESC,SAMP_RES,SRC_ACC&returnGeometry=true&outSR=4326&geometryPrecision=6&f=geojson",
            self.metadata
        )
    }
}

/// The releases, oldest first: the baked list, or Esri's live one.
#[derive(Clone, Debug)]
pub struct Releases {
    pub list: Vec<Release>,
    /// Whether the list was read from Esri just now.
    pub live: bool,
}

impl Releases {
    /// The releases baked into the plugin.
    pub fn baked() -> Releases {
        let list = RELEASES.iter().map(|&(num, date, metadata)| Release { num, date: date.into(), metadata: metadata.into() }).collect();
        Releases { list, live: false }
    }

    /// The releases in a `service.layers` answer for Wayback's capabilities, with the baked
    /// ones it lacks. None when it names none.
    pub fn from_layers(answer: &Value) -> Option<Releases> {
        let mut out = Releases::baked();
        let mut found = false;
        for l in answer["layers"].as_array()? {
            let Some(r) = parse_layer(l) else { continue };
            found = true;
            // A baked release keeps its metadata service: Esri's layer name is wrong for one.
            if !out.list.iter().any(|b| b.num == r.num) {
                out.list.push(r);
            }
        }
        out.list.sort_by(|a, b| a.date.cmp(&b.date));
        out.live = true;
        found.then_some(out)
    }

    /// Esri's live list when `live` and it can be read, else the baked one.
    fn load(live: bool) -> Releases {
        if live {
            match host::run("service.layers", json!({"url": CAPABILITIES, "type": "wmts"})) {
                Ok(answer) => {
                    if let Some(r) = Releases::from_layers(&answer) {
                        return r;
                    }
                    host::log("Wayback's capabilities named no releases; using the plugin's list");
                }
                Err(e) => host::log(&format!("couldn't read Wayback's releases ({e}); using the plugin's list")),
            }
        }
        Releases::baked()
    }

    pub fn first(&self) -> &Release {
        &self.list[0]
    }

    pub fn last(&self) -> &Release {
        &self.list[self.list.len() - 1]
    }

    pub fn find(&self, num: u32) -> Option<&Release> {
        self.list.iter().find(|r| r.num == num)
    }

    pub fn index(&self, num: u32) -> Option<usize> {
        self.list.iter().position(|r| r.num == num)
    }

    /// The last release on or before a date (`YYYY-MM-DD`, `YYYY-MM` or `YYYY`; a shorter date
    /// means the end of its month or year).
    pub fn on_or_before(&self, date: &str) -> Option<&Release> {
        let d = date.trim();
        // Compared as text: `2019` is past every `2019-…` date that starts with it.
        valid_date(d).then(|| self.list.iter().rev().find(|r| r.date.as_str() <= d || r.date.starts_with(d)))?
    }

    /// Whether a date may reach past this list (a newer release could be on or before it).
    pub fn reaches_past(&self, date: &str) -> bool {
        let (d, last) = (date.trim(), self.last().date.as_str());
        valid_date(d) && (d > last || last.starts_with(d))
    }
}

fn valid_date(d: &str) -> bool {
    matches!(d.len(), 4 | 7 | 10) && d.chars().enumerate().all(|(i, c)| if i == 4 || i == 7 { c == '-' } else { c.is_ascii_digit() })
}

/// A release from one of Wayback's WMTS layers: `WB_2026_R09`, titled `World Imagery (Wayback
/// 2026-09-24)`, with tiles at `…/tile/63116/{z}/{y}/{x}`.
pub fn parse_layer(l: &Value) -> Option<Release> {
    let (year, n) = l["name"].as_str()?.strip_prefix("WB_")?.split_once("_R")?;
    let date = l["title"].as_str()?.split("Wayback ").nth(1)?.get(..10)?;
    let num = l["offer"]["url"].as_str()?.split("/tile/").nth(1)?.split('/').next()?.parse().ok()?;
    // The name goes into the metadata address's path: only a year and a release's digits.
    let digits = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit());
    (year.len() == 4 && digits(year) && n.len() <= 4 && digits(n) && valid_date(date)).then(|| Release {
        num,
        date: date.into(),
        metadata: format!("World_Imagery_Metadata_{year}_r{}", n.to_lowercase()),
    })
}

fn handle(req: Request) -> Result<Value, String> {
    let add = req.bool("add").unwrap_or(false);
    let top = on_map()?.first().copied();
    // Esri's live list is read only when the baked one may be out of date for this command.
    let baked = Releases::baked();
    let unknown = top.is_some_and(|n| baked.find(n).is_none());
    // A release on the map that the baked list lacks is newer than it: read the live list.
    let live = unknown
        || match req.id.as_str() {
            "latest" | "releases" => true,
            "newer" => top.and_then(|n| baked.index(n)).is_none_or(|i| i + 1 == baked.list.len()),
            "older" | "info" => false,
            "release" => match (req.f64("release"), req.str("date")) {
                (Some(n), _) => !baked.list.iter().any(|r| f64::from(r.num) == n),
                (None, Some(d)) => baked.reaches_past(d),
                (None, None) => false,
            },
            id => id.strip_prefix("year-").is_some_and(|y| baked.reaches_past(y)),
        };
    let all = if live { Releases::load(true) } else { baked };
    match req.id.as_str() {
        "latest" => show(&req, &all, top, all.last(), add),
        "oldest" => show(&req, &all, top, all.first(), add),
        "older" | "newer" => step(&req, &all, top, req.id == "newer", add),
        "release" => {
            let r = match (req.f64("release"), req.str("date")) {
                (Some(n), _) => all.list.iter().find(|r| f64::from(r.num) == n),
                (None, Some(d)) => all.on_or_before(d),
                (None, None) => return Err(req.tr(MANIFEST, "Give a date (\"date\": \"2019-05-08\") or a release number.", &[])),
            };
            let r = r.ok_or_else(|| req.tr(MANIFEST, "No release matches that: the first is {date}.", &[("date", &all.first().date)]))?;
            show(&req, &all, top, r, add)
        }
        "info" => info(&req, top, top.and_then(|n| all.find(n))),
        "releases" => list(&req, &all),
        "clear" => clear(&req),
        other => match other.strip_prefix("year-").and_then(|y| all.on_or_before(y)) {
            Some(r) => show(&req, &all, top, r, add),
            None => Err(format!("Historical Imagery has no command '{other}'")),
        },
    }
}

rustgis_plugin::export!(handle);

/// The release numbers of the plugin's tile layers on the map, top first.
fn on_map() -> Result<Vec<u32>, String> {
    let tiles = host::run("map.tiles", json!({}))?;
    let tiles = tiles.as_array().ok_or("map.tiles returned an unexpected result")?;
    Ok(tiles.iter().filter_map(|t| t["id"].as_str()?.strip_prefix(ID)?.parse().ok()).collect())
}

/// Adds a release, replacing the one on top unless `add`.
/// `top` is the release number on top, which may be one neither list knows.
fn show(req: &Request, all: &Releases, top: Option<u32>, r: &Release, add: bool) -> Result<Value, String> {
    let done = json!({"release": r.num, "date": r.date, "live": all.live});
    if top == Some(r.num) {
        host::notify(&req.tr(MANIFEST, "Wayback {date} is already on the map.", &[("date", &r.date)]));
        return Ok(done);
    }
    // One layer per release: one stacked lower down stays with `add`, and otherwise comes to the top.
    let stacked = top.is_some() && on_map()?.contains(&r.num);
    if add && stacked {
        host::notify(&req.tr(MANIFEST, "Wayback {date} is already on the map.", &[("date", &r.date)]));
        return Ok(done);
    }
    if let (Some(n), false) = (top, add) {
        host::run("map.removeTiles", json!({"id": format!("{ID}{n}")}))?;
    }
    if stacked {
        host::run("map.removeTiles", json!({"id": r.id()}))?;
    }
    host::run(
        "map.addTiles",
        json!({
            "url": r.tiles(), "kind": "raster", "name": r.name(), "attribution": ATTRIBUTION, "maxZoom": 19,
            "overlay": false, "replace": false, "id": r.id(),
        }),
    )?;
    let n = all.index(r.num).unwrap_or(0) + 1;
    host::notify(&req.tr(
        MANIFEST,
        "Wayback {date} (release {n} of {total})",
        &[("date", &r.date), ("n", &n.to_string()), ("total", &all.list.len().to_string())],
    ));
    Ok(done)
}

/// The release after (or before) the one on top, or the latest when there is none.
fn step(req: &Request, all: &Releases, top: Option<u32>, newer: bool, add: bool) -> Result<Value, String> {
    let Some(cur) = top.and_then(|n| all.find(n)) else {
        return show(req, all, top, all.last(), add);
    };
    let i = all.index(cur.num).unwrap_or(0);
    let next = if newer { all.list.get(i + 1) } else { i.checked_sub(1).map(|i| &all.list[i]) };
    match next {
        Some(r) => show(req, all, top, r, add),
        None => {
            let date = &[("date", cur.date.as_str())];
            host::notify(&if newer {
                req.tr(MANIFEST, "Wayback {date} is the latest release.", date)
            } else {
                req.tr(MANIFEST, "Wayback {date} is the first release.", date)
            });
            Ok(json!({"release": cur.num, "date": cur.date, "live": all.live}))
        }
    }
}

/// The web map zoom level of a `host.view` (its scale is true at the centre's latitude, at
/// 0.28 mm a pixel).
pub fn zoom_of(scale: f64, lat: f64) -> f64 {
    let merc_m_per_px = scale * 0.000_28 / lat.to_radians().cos().abs().max(1e-6);
    (156_543.033_92 / merc_m_per_px).log2()
}

/// The imagery source at a place in the release on top.
/// `top` is the release number on top and `release` its release, when a list knows it.
fn info(req: &Request, top: Option<u32>, release: Option<&Release>) -> Result<Value, String> {
    let Some(r) = release else {
        return Err(match top {
            Some(n) => req.tr(
                MANIFEST,
                "Release {n} is newer than the plugin, and Esri's list of releases couldn't be read.",
                &[("n", &n.to_string())],
            ),
            None => req.tr(MANIFEST, "Add a Wayback release first (Wayback › Latest).", &[]),
        });
    };
    let view = host::view()?;
    let center = |i: usize| view["center"].get(i).and_then(Value::as_f64);
    let (lon, lat) = match (req.f64("lon").or(center(0)), req.f64("lat").or(center(1))) {
        (Some(lon), Some(lat)) if lon.is_finite() && lat.is_finite() => (lon, lat.clamp(-85.0, 85.0)),
        _ => return Err("the map view has no centre".into()),
    };
    let zoom = view["scale"].as_f64().filter(|s| s.is_finite() && *s > 0.0).map_or(10.0, |s| zoom_of(s, lat));
    let name = format!("{SOURCE}{}", r.name());
    // One imagery source at a time: the earlier ones go once the new one is in.
    let earlier = sources()?;
    let out = host::run("layer.addUrl", json!({"url": r.metadata_url(lon, lat, zoom), "name": name, "zoom": false}))?;
    remove_layers(&earlier)?;
    if out.get("pending").and_then(Value::as_bool) == Some(true) {
        // The web app downloads it off the frame: its details land in the layer's table.
        host::notify(&req.tr(
            MANIFEST,
            "Looking up the imagery source in Wayback {date}: its capture date, source and resolution will be in the new layer's table.",
            &[("date", &r.date)],
        ));
        return Ok(out);
    }
    // The answer is the added layer's id; its one feature is the source. Added while the earlier
    // one was still there, it took a numbered name: it gets the plain one back.
    let added = out["layer"].clone();
    let features = if added.is_null() { None } else { host::call("host.features", json!({"layer": added})).ok() };
    if !added.is_null() {
        host::run("layer.rename", json!({"layer": added, "name": name}))?;
    }
    let layer = name;
    let props = features.as_ref().and_then(|f| f["features"].get(0)).map(|f| f["properties"].clone());
    let Some(p) = props else {
        host::notify(&req.tr(MANIFEST, "Wayback {date} has no imagery source here.", &[("date", &r.date)]));
        return Ok(json!({"release": r.num, "date": r.date, "source": null}));
    };
    let captured = p["SRC_DATE2"].as_f64().map(date_of).unwrap_or_default();
    let source =
        [p["NICE_DESC"].as_str(), p["SRC_DESC"].as_str()].into_iter().flatten().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" ");
    let res = p["SAMP_RES"].as_f64().map(|m| format!("{m}")).unwrap_or_default();
    host::notify(&req.tr(
        MANIFEST,
        "Captured {captured} by {source}, {res} m a pixel (in Wayback {date})",
        &[("captured", &captured), ("source", &source), ("res", &res), ("date", &r.date)],
    ));
    Ok(json!({
        "release": r.num, "date": r.date, "layer": layer,
        "source": {"captured": captured, "provider": p["NICE_DESC"], "sensor": p["SRC_DESC"], "resolution": p["SAMP_RES"], "accuracy": p["SRC_ACC"]},
    }))
}

/// A date (`YYYY-MM-DD`) from milliseconds since 1970.
pub fn date_of(ms: f64) -> String {
    let days = (ms / 86_400_000.0).floor() as i64;
    // Howard Hinnant's days-to-civil.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

fn list(req: &Request, all: &Releases) -> Result<Value, String> {
    let shown: Vec<&str> = on_map()?.into_iter().filter_map(|n| all.find(n)).map(|r| r.date.as_str()).collect();
    let args = [
        ("total", all.list.len().to_string()),
        ("first", all.first().date.clone()),
        ("last", all.last().date.clone()),
        ("shown", shown.join(", ")),
    ];
    let args: Vec<(&str, &str)> = args.iter().map(|(k, v)| (*k, v.as_str())).collect();
    let mut text = if shown.is_empty() {
        req.tr(MANIFEST, "{total} releases, {first} to {last}. None is on the map.", &args)
    } else {
        req.tr(MANIFEST, "{total} releases, {first} to {last}. On the map: {shown}.", &args)
    };
    if !all.live {
        text.push(' ');
        text.push_str(&req.tr(MANIFEST, "(Esri's list couldn't be read: these are the releases the plugin knows.)", &[]));
    }
    host::notify(&text);
    let releases: Vec<Value> = all.list.iter().map(|r| json!({"release": r.num, "date": r.date})).collect();
    Ok(json!({"releases": releases, "onMap": shown, "live": all.live}))
}

/// The name prefix of the imagery source layers Imagery Info adds.
const SOURCE: &str = "Imagery source · ";

/// Whether a layer name is one Imagery Info gives: `Imagery source · Wayback 2019-05-08`.
pub fn is_source(name: &str) -> bool {
    name.strip_prefix(SOURCE).and_then(|n| n.strip_prefix("Wayback ")).is_some_and(|d| d.len() == 10 && valid_date(d))
}

/// Removes the imagery source layers Imagery Info added.
fn remove_sources() -> Result<usize, String> {
    let ids = sources()?;
    remove_layers(&ids)?;
    Ok(ids.len())
}

/// The ids of the imagery source layers on the map.
fn sources() -> Result<Vec<Value>, String> {
    let layers = host::layers()?;
    Ok(layers.as_array().into_iter().flatten().filter(|l| l["name"].as_str().is_some_and(is_source)).map(|l| l["id"].clone()).collect())
}

fn remove_layers(ids: &[Value]) -> Result<(), String> {
    for id in ids {
        host::run("layer.remove", json!({"layer": id}))?;
    }
    Ok(())
}

fn clear(req: &Request) -> Result<Value, String> {
    let shown = on_map()?;
    let sources = remove_sources()?;
    for n in &shown {
        host::run("map.removeTiles", json!({"id": format!("{ID}{n}")}))?;
    }
    host::notify(&if shown.is_empty() && sources == 0 {
        req.tr(MANIFEST, "No Wayback imagery on the map.", &[])
    } else {
        req.tr(MANIFEST, "Removed the Wayback imagery.", &[])
    });
    Ok(json!({"removed": shown}))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustgis_plugin::serde_json;

    #[test]
    fn releases_and_manifest_agree() {
        let manifest: Value = serde_json::from_str(MANIFEST).unwrap();
        let commands: Vec<&str> = manifest["commands"].as_array().unwrap().iter().filter_map(|c| c["id"].as_str()).collect();
        assert!(RELEASES.windows(2).all(|w| w[0].1 < w[1].1), "releases are oldest first");
        let mut years: Vec<&str> = RELEASES.iter().map(|r| &r.1[..4]).collect();
        years.dedup();
        for y in &years {
            assert!(commands.contains(&format!("year-{y}").as_str()), "{y} has no command");
        }
        for c in commands {
            let year = c.strip_prefix("year-").is_some_and(|y| years.contains(&y));
            assert!(
                year || ["latest", "older", "newer", "oldest", "release", "info", "releases", "clear"].contains(&c),
                "{c} isn't handled"
            );
        }
        assert_eq!(manifest["permissions"], json!(["edit", "network"]));
    }

    #[test]
    fn finds_releases_by_date() {
        let all = Releases::baked();
        assert_eq!(all.on_or_before("2014").unwrap().date[..4], *"2014");
        assert_eq!(all.on_or_before("2014-02-20").unwrap().num, 10);
        assert_eq!(all.on_or_before("2014-02").unwrap().num, 10);
        assert!(all.on_or_before("2014-02-19").is_none());
        assert!(all.on_or_before("2013").is_none());
        assert_eq!(all.on_or_before("2100-01-01"), Some(all.last()));
        assert!(all.on_or_before("2019-5").is_none() && all.on_or_before("soon").is_none());
        let r = all.on_or_before("2019-06-30").unwrap();
        assert!(r.date.as_str() <= "2019-06-30" && all.list[all.index(r.num).unwrap() + 1].date.as_str() > "2019-06-30");
        let last = all.last().date.clone();
        assert!(all.reaches_past(&last[..4]) && all.reaches_past("2100") && !all.reaches_past("2019") && !all.reaches_past("soon"));
    }

    #[test]
    fn reads_the_live_list() {
        let layer = |name: &str, date: &str, num: u32| {
            json!({"name": name, "title": format!("World Imagery (Wayback {date})"),
                "offer": {"type": "tiles", "url": format!("https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{num}/{{z}}/{{y}}/{{x}}")}})
        };
        // A release newer than the baked list, and a baked one whose layer name is off.
        let answer =
            json!({"layers": [layer("WB_2100_R01", "2100-01-15", 99999), layer("WB_2015_R14", "2015-03-18", 15084), {"name": "other"}]});
        let all = Releases::from_layers(&answer).unwrap();
        assert!(all.live);
        assert_eq!(all.list.len(), RELEASES.len() + 1);
        assert_eq!(all.last(), &Release { num: 99999, date: "2100-01-15".into(), metadata: "World_Imagery_Metadata_2100_r01".into() });
        assert_eq!(all.find(15084).unwrap().metadata, "World_Imagery_Metadata_2015_r04");
        assert!(Releases::from_layers(&json!({"layers": []})).is_none());
        // A name that would reach outside the metadata service's path is skipped.
        for bad in ["WB_2100_R01/../x", "WB_21a0_R01", "WB_2100_R0?f=x", "WB_2100_R"] {
            assert!(parse_layer(&layer(bad, "2100-01-15", 99999)).is_none(), "{bad}");
        }
        assert!(Releases::from_layers(&json!({"error": "x"})).is_none());
    }

    #[test]
    fn builds_addresses() {
        let all = Releases::baked();
        let r = all.first();
        assert_eq!(r.id(), "wayback-10");
        assert!(r.tiles().ends_with("/tile/10/{z}/{y}/{x}"));
        let u = r.metadata_url(-84.39, 33.75, 18.2);
        assert!(u.contains("World_Imagery_Metadata_2014_r01/MapServer/5/query?") && u.contains("geometry=-84.390000,33.750000"), "{u}");
        assert!(r.metadata_url(0.0, 0.0, 3.0).contains("/MapServer/13/query"));
        assert!(is_source(&format!("{SOURCE}{}", r.name())));
        assert!(!is_source("Imagery source · my notes") && !is_source("Imagery source · Wayback 2019-05-08 copy"));
        assert!(r.metadata_url(0.0, 0.0, 25.0).contains("/MapServer/0/query"));
    }

    #[test]
    fn converts_scale_and_dates() {
        // Zoom 0 at the equator is 156,543 m a pixel: a scale of 1:559,082,264.
        assert!(zoom_of(559_082_264.0, 0.0).abs() < 1e-3);
        assert!((zoom_of(559_082_264.0 / 1024.0 * 60f64.to_radians().cos(), 60.0) - 10.0).abs() < 1e-3);
        assert_eq!(date_of(1_769_990_400_000.0), "2026-02-02");
        assert_eq!(date_of(0.0), "1970-01-01");
    }
}
