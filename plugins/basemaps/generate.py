"""Generates the Basemaps plugin's plugin.json and src/catalog.rs from one table.

    python plugins/basemaps/generate.py && cargo fmt -p basemaps

Edit the table (B) and the tab layout here, never the two generated files.
"""

import json
import pathlib

root = pathlib.Path(__file__).resolve().parent
OSM = "© OpenStreetMap contributors"
ESRI = "Tiles © Esri"
USGS = "USGS The National Map"
NASA = "Imagery: NASA Global Imagery Browse Services (GIBS), part of EOSDIS"
AGS = "https://server.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
USGS_T = "https://basemap.nationalmap.gov/arcgis/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{}/default//GoogleMapsCompatible_Level8/{{z}}/{{y}}/{{x}}.{}"
OFM = "https://tiles.openfreemap.org/styles/{}"
VT = "https://tiles.versatiles.org/assets/styles/{}/style.json"

# id, label, help, kind, url, attribution, max zoom, overlay, subdomains, icon
B = [
    ("osm", "OpenStreetMap", "The standard OpenStreetMap map.", "raster", "https://tile.openstreetmap.org/{z}/{x}/{y}.png", OSM, 19, False, "", "basemap"),
    ("osm-hot", "OSM Humanitarian", "OpenStreetMap in the Humanitarian OpenStreetMap Team's style.", "raster", "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png", OSM + ", tiles style by Humanitarian OpenStreetMap Team hosted by OpenStreetMap France", 19, False, "ab", "basemap"),
    ("osm-de", "OSM Germany", "OpenStreetMap in the German style.", "raster", "https://tile.openstreetmap.de/{z}/{x}/{y}.png", OSM, 18, False, "", "basemap"),
    ("osm-fr", "OSM France", "OpenStreetMap in the French style.", "raster", "https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", "© OpenStreetMap France | " + OSM, 20, False, "abc", "basemap"),
    ("cyclosm", "CyclOSM", "OpenStreetMap for cycling: bike lanes, routes and parking.", "raster", "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png", "CyclOSM | Map data: " + OSM, 20, False, "abc", "basemap"),
    ("opentopomap", "OpenTopoMap", "A topographic map from OpenStreetMap and SRTM elevation.", "raster", "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", "Map data: " + OSM + ", SRTM | Map style: © OpenTopoMap (CC-BY-SA)", 17, False, "abc", "relief"),
    ("esri-streets", "Esri World Street Map", "Esri's street map of the world.", "raster", AGS.format("World_Street_Map"), ESRI + " — Source: Esri and its data partners", 19, False, "", "basemap"),
    ("esri-topo", "Esri World Topographic", "Esri's topographic map of the world.", "raster", AGS.format("World_Topo_Map"), ESRI + " — Source: Esri and its data partners", 19, False, "", "relief"),
    ("esri-light-gray", "Esri Light Gray Canvas", "Esri's light grey canvas, for data to stand out on.", "raster", AGS.format("Canvas/World_Light_Gray_Base"), ESRI + " — Esri, HERE, Garmin, © OpenStreetMap contributors", 16, False, "", "basemap"),
    ("esri-dark-gray", "Esri Dark Gray Canvas", "Esri's dark grey canvas.", "raster", AGS.format("Canvas/World_Dark_Gray_Base"), ESRI + " — Esri, HERE, Garmin, © OpenStreetMap contributors", 16, False, "", "dark"),
    ("esri-natgeo", "Esri National Geographic", "The National Geographic style world map.", "raster", AGS.format("NatGeo_World_Map"), ESRI + " — National Geographic, Esri, DeLorme, NAVTEQ, UNEP-WCMC, USGS, NASA, ESA, METI, NRCAN, GEBCO, NOAA, iPC", 16, False, "", "basemap"),
    ("esri-ocean", "Esri Ocean", "Esri's ocean basemap with bathymetry.", "raster", AGS.format("Ocean/World_Ocean_Base"), ESRI + " — Sources: GEBCO, NOAA, CHS, OSU, UNH, CSUMB, National Geographic, DeLorme, NAVTEQ, and Esri", 13, False, "", "basemap"),
    ("esri-imagery", "Esri World Imagery", "Satellite and aerial imagery of the world.", "raster", AGS.format("World_Imagery"), ESRI + " — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community", 19, False, "", "raster_layer"),
    ("usgs-imagery", "USGS Imagery", "The National Map's orthoimagery (United States, and the world at small scales).", "raster", USGS_T.format("USGSImageryOnly"), USGS, 16, False, "", "raster_layer"),
    ("usgs-imagery-topo", "USGS Imagery Topo", "The National Map's orthoimagery with topographic labels.", "raster", USGS_T.format("USGSImageryTopo"), USGS, 16, False, "", "raster_layer"),
    ("nasa-blue-marble", "NASA Blue Marble", "NASA's Blue Marble: the Earth's surface with shaded relief and bathymetry.", "raster", GIBS.format("BlueMarble_ShadedRelief_Bathymetry", "jpeg"), NASA, 8, False, "", "globe_view"),
    ("nasa-night-lights", "NASA Earth at Night", "City lights at night from VIIRS (2012).", "raster", GIBS.format("VIIRS_CityLights_2012", "jpg"), NASA, 8, False, "", "dark"),
    ("usgs-topo", "USGS Topo", "The National Map's topographic basemap (United States).", "raster", USGS_T.format("USGSTopo"), USGS, 16, False, "", "relief"),
    ("usgs-shaded-relief", "USGS Shaded Relief", "The National Map's shaded relief (United States).", "raster", USGS_T.format("USGSShadedReliefOnly"), USGS, 16, False, "", "relief"),
    ("esri-terrain", "Esri World Terrain", "Esri's terrain basemap: relief and bathymetry.", "raster", AGS.format("World_Terrain_Base"), ESRI + " — Source: USGS, Esri, TANA, DeLorme, and NPS", 13, False, "", "relief"),
    ("esri-shaded-relief", "Esri Shaded Relief", "Esri's shaded relief of the world.", "raster", AGS.format("World_Shaded_Relief"), ESRI + " — Source: Esri", 13, False, "", "relief"),
    ("esri-physical", "Esri Physical", "Esri's physical map: natural vegetation and relief.", "raster", AGS.format("World_Physical_Map"), ESRI + " — Source: US National Park Service", 8, False, "", "relief"),
    ("esri-hillshade", "Esri World Hillshade", "Esri's hillshade of the world's elevation.", "raster", AGS.format("Elevation/World_Hillshade"), ESRI + " — Sources: Esri, Airbus DS, USGS, NGA, NASA, CGIAR, N Robinson, NCEAS, NLS, OS, NMA, Geodatastyrelsen, Rijkswaterstaat, GSA, Geoland, FEMA, Intermap and the GIS user community", 16, False, "", "relief"),
    ("openfreemap-liberty", "OpenFreeMap Liberty", "OpenFreeMap's Liberty: a detailed vector map of the world.", "style", OFM.format("liberty"), "", 14, False, "", "web_map"),
    ("openfreemap-bright", "OpenFreeMap Bright", "OpenFreeMap's Bright vector style.", "style", OFM.format("bright"), "", 14, False, "", "web_map"),
    ("openfreemap-positron", "OpenFreeMap Positron", "OpenFreeMap's light Positron vector style.", "style", OFM.format("positron"), "", 14, False, "", "web_map"),
    ("openfreemap-dark", "OpenFreeMap Dark", "OpenFreeMap's dark vector style.", "style", OFM.format("dark"), "", 14, False, "", "dark"),
    ("openfreemap-fiord", "OpenFreeMap Fiord", "OpenFreeMap's blue-grey Fiord vector style.", "style", OFM.format("fiord"), "", 14, False, "", "web_map"),
    ("openfreemap-tiles", "OpenFreeMap Tiles (RustGIS Style)", "OpenFreeMap's vector tiles in RustGIS's own built-in style.", "vector", "https://tiles.openfreemap.org/planet", "OpenFreeMap © OpenMapTiles " + OSM, 14, False, "", "web_map"),
    ("versatiles-colorful", "VersaTiles Colorful", "VersaTiles' colourful vector style.", "style", VT.format("colorful"), "", 14, False, "", "web_map"),
    ("versatiles-eclipse", "VersaTiles Eclipse", "VersaTiles' dark vector style.", "style", VT.format("colorful-dark"), "", 14, False, "", "dark"),
    ("versatiles-graybeard", "VersaTiles Graybeard", "VersaTiles' grey vector style.", "style", VT.format("gray"), "", 14, False, "", "web_map"),
    ("versatiles-shadow", "VersaTiles Shadow", "VersaTiles' dark grey vector style.", "style", VT.format("gray-dark"), "", 14, False, "", "dark"),
    ("versatiles-neutrino", "VersaTiles Neutrino", "VersaTiles' muted vector style.", "style", VT.format("muted"), "", 14, False, "", "web_map"),
    ("esri-places", "Esri Boundaries and Places", "Esri's boundaries and place names, for imagery.", "raster", AGS.format("Reference/World_Boundaries_and_Places"), ESRI + " — Source: Esri and its data partners", 19, True, "", "label"),
    ("esri-gray-reference", "Esri Light Gray Reference", "The labels of Esri's light grey canvas.", "raster", AGS.format("Canvas/World_Light_Gray_Reference"), ESRI + " — Esri, HERE, Garmin, © OpenStreetMap contributors", 16, True, "", "label"),
    ("esri-ocean-reference", "Esri Ocean Reference", "Names of seas and undersea features.", "raster", AGS.format("Ocean/World_Ocean_Reference"), ESRI + " — Sources: GEBCO, NOAA, CHS, OSU, UNH, CSUMB, National Geographic, DeLorme, NAVTEQ, and Esri", 13, True, "", "label"),
    ("esri-transportation", "Esri Transportation", "Roads and railways, for imagery.", "raster", AGS.format("Reference/World_Transportation"), ESRI + " — Source: Esri and its data partners", 19, True, "", "line_layer"),
    ("openrailwaymap", "OpenRailwayMap", "Railways from OpenStreetMap.", "raster", "https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png", "Map data: " + OSM + " | Map style: © OpenRailwayMap (CC-BY-SA)", 19, True, "abc", "line_layer"),
    ("openseamap", "OpenSeaMap", "Sea marks: buoys, lights and harbours.", "raster", "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png", "Map data: © OpenSeaMap contributors", 18, True, "", "point_layer"),
    ("waymarked-hiking", "Hiking Trails", "Waymarked hiking routes from OpenStreetMap.", "raster", "https://tile.waymarkedtrails.org/hiking/{z}/{x}/{y}.png", "Map data: " + OSM + " | Map style: © waymarkedtrails.org (CC-BY-SA)", 18, True, "", "line_layer"),
    ("waymarked-cycling", "Cycling Routes", "Waymarked cycling routes from OpenStreetMap.", "raster", "https://tile.waymarkedtrails.org/cycling/{z}/{x}/{y}.png", "Map data: " + OSM + " | Map style: © waymarkedtrails.org (CC-BY-SA)", 18, True, "", "line_layer"),
    ("usgs-hydro", "USGS Hydrography", "The National Map's rivers, streams and water bodies (United States).", "raster", USGS_T.format("USGSHydroCached"), USGS, 16, False, "", "basemap"),
]
EXTRA = [
    {"id": "clear", "label": "Clear\nBasemaps", "help": "Remove the web basemaps and go back to the built-in one. Overlays stay.", "icon": "delete"},
    {"id": "clear-overlays", "label": "Clear\nOverlays", "help": "Remove the overlays (labels, roads, trails).", "icon": "clear_sel"},
    {"id": "current", "label": "Current", "help": "Show the map's tile layers in the status bar.", "icon": "stats"},
]
ids = [b[0] for b in B]
assert len(set(ids)) == len(ids)

def menu(label, icon, help, items):
    assert 1 <= len(items) <= 32 and all(i in ids for i in items), label
    return {"menu": label, "icon": icon, "help": help, "items": items}

overlays = [b[0] for b in B if b[7]]
tab = {"label": "Basemaps", "groups": [
    {"label": "Popular", "items": ["osm", "esri-imagery", "openfreemap-positron", "openfreemap-liberty"]},
    {"label": "Streets", "items": [
        menu("OpenStreetMap", "basemap", "OpenStreetMap and maps made from it.", ["osm", "osm-hot", "osm-de", "osm-fr", "cyclosm", "opentopomap"]),
        menu("Esri", "basemap", "Esri's street, topographic and canvas maps.", ["esri-streets", "esri-topo", "esri-light-gray", "esri-dark-gray", "esri-natgeo", "esri-ocean"]),
    ]},
    {"label": "Imagery & Terrain", "items": [
        menu("Imagery", "raster_layer", "Satellite and aerial imagery.", ["esri-imagery", "usgs-imagery", "usgs-imagery-topo", "nasa-blue-marble", "nasa-night-lights"]),
        menu("Terrain", "relief", "Topographic maps, relief and hillshade.", ["opentopomap", "usgs-topo", "usgs-shaded-relief", "esri-terrain", "esri-shaded-relief", "esri-physical", "esri-hillshade", "usgs-hydro"]),
    ]},
    {"label": "Vector", "items": [
        menu("OpenFreeMap", "web_map", "OpenFreeMap's vector maps of the world, drawn from vector tiles.", [i for i in ids if i.startswith("openfreemap-")]),
        menu("VersaTiles", "web_map", "VersaTiles' vector maps of the world.", [i for i in ids if i.startswith("versatiles-")]),
    ]},
    {"label": "Overlays", "items": [
        menu("Labels", "label", "Place names over any basemap. Choose one again to remove it.", ["esri-places", "esri-gray-reference", "esri-ocean-reference"]),
        menu("Lines", "line_layer", "Roads, railways, trails and waterways over any basemap. Choose one again to remove it.", ["esri-transportation", "openrailwaymap", "waymarked-hiking", "waymarked-cycling", "openseamap"]),
    ]},
    {"label": "Manage", "items": ["clear", "clear-overlays", "current"]},
]}
used = {i for g in tab["groups"] for it in g["items"] for i in (it["items"] if isinstance(it, dict) else [it])}
missing = set(ids) - used
assert not missing, missing

PARAMS = '{"add"?: bool (stack it over the current basemaps instead of replacing them)}'
commands = []
for (i, label, help, kind, url, att, mz, ov, subs, icon) in B:
    h = help + (" An overlay: choose it again to remove it." if ov else "")
    c = {"id": i, "label": label, "help": h, "icon": icon}
    if not ov:
        c["params"] = PARAMS
    commands.append(c)
commands += EXTRA

de = {"Basemaps": "Grundkarten", "Popular": "Beliebt", "Streets": "Straßen", "Imagery & Terrain": "Bilder & Gelände", "Vector": "Vektor",
      "Overlays": "Überlagerungen", "Manage": "Verwalten", "Imagery": "Luftbilder", "Terrain": "Gelände", "Labels": "Beschriftungen", "Lines": "Linien",
      "Clear\nBasemaps": "Grundkarten\nentfernen", "Clear\nOverlays": "Überlagerungen\nentfernen", "Current": "Aktuell",
      "Hiking Trails": "Wanderwege", "Cycling Routes": "Radrouten", "OSM Germany": "OSM Deutschland", "OSM France": "OSM Frankreich", "OSM Humanitarian": "OSM Humanitär",
      "NASA Earth at Night": "NASA Erde bei Nacht"}
zh = {"Basemaps": "底图", "Popular": "常用", "Streets": "街道", "Imagery & Terrain": "影像与地形", "Vector": "矢量",
      "Overlays": "叠加层", "Manage": "管理", "Imagery": "影像", "Terrain": "地形", "Labels": "注记", "Lines": "线状要素",
      "Clear\nBasemaps": "清除\n底图", "Clear\nOverlays": "清除\n叠加层", "Current": "当前",
      "Hiking Trails": "徒步路线", "Cycling Routes": "骑行路线", "OSM Germany": "OSM 德国", "OSM France": "OSM 法国", "OSM Humanitarian": "OSM 人道主义",
      "NASA Earth at Night": "NASA 夜间地球", "OpenStreetMap": "OpenStreetMap"}
manifest = {
    "id": "basemaps", "name": "Basemaps", "version": "0.1.0",
    "description": "A tab of public basemaps that need no API key: OpenStreetMap, Esri, USGS and NASA raster tiles, OpenFreeMap and VersaTiles vector maps, and overlays for labels, roads, railways and trails. Basemaps replace each other; overlays stack on top.",
    "author": "opengeos", "homepage": "https://github.com/opengeos/rustgis-plugins/tree/main/plugins/basemaps",
    "license": "MIT", "entry": "plugin.wasm", "abi": 1, "minRustgisVersion": "0.1.0",
    "permissions": ["edit", "network"],
    "commands": commands, "tab": tab, "i18n": {"de": de, "zh-CN": zh},
}
(root / "plugin.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

def rs(s):
    return json.dumps(s, ensure_ascii=False)
lines = ["//! The catalog: every basemap and overlay the plugin offers. Generated by `generate.py` with",
         "//! `plugin.json`'s commands from one table, so the two list the same ids (a test checks they do).", "",
         "use crate::Basemap;", "", "pub const CATALOG: &[Basemap] = &["]
for (i, label, help, kind, url, att, mz, ov, subs, icon) in B:
    lines.append(f"    Basemap {{ id: {rs(i)}, name: {rs(label)}, kind: {rs(kind)}, url: {rs(url)}, attribution: {rs(att)}, max_zoom: {mz}, overlay: {'true' if ov else 'false'}, subdomains: {rs(subs)} }},")
lines.append("];")
(root / "src" / "catalog.rs").write_text("\n".join(lines) + "\n")
print(len(B), "basemaps,", len(commands), "commands")
