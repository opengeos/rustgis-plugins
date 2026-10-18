"""Generates the Historical Imagery plugin's plugin.json and src/releases.rs from Esri's list of
World Imagery Wayback releases.

    python plugins/historical-imagery/generate.py [waybackconfig.json]

Without a file it downloads the list from Esri. Run it again when Esri publishes new releases
(about once a month), and bump the version: the plugin reads Esri's live list for the newest
releases anyway, but the baked list makes stepping fast, works offline and gives each year its
command. Edit the commands and the tab here, never the two
generated files. The translations in plugin.json's `i18n` are kept; fill in new strings with
`python scripts/i18n.py missing` and `insert`.
"""

import json
import pathlib
import re
import sys
import urllib.request

root = pathlib.Path(__file__).resolve().parent
CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
TILES = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{}/{{level}}/{{row}}/{{col}}"
METADATA = re.compile(r"^https://metadata\.maptiles\.arcgis\.com/arcgis/rest/services/([A-Za-z0-9_]+)/MapServer$")

if len(sys.argv) > 1:
    config = json.loads(pathlib.Path(sys.argv[1]).read_text())
else:
    with urllib.request.urlopen(CONFIG, timeout=60) as r:
        config = json.load(r)

# (release number, date, metadata service), oldest first.
releases = []
for num, item in config.items():
    found = re.search(r"\d{4}-\d\d-\d\d", item["itemTitle"])
    assert found, f"release {num}: no date in its title {item['itemTitle']!r}"
    date = found.group()
    assert item["itemURL"] == TILES.format(num), item["itemURL"]
    meta = METADATA.match(item["metadataLayerUrl"])
    assert meta, item["metadataLayerUrl"]
    releases.append((int(num), date, meta.group(1)))
releases.sort(key=lambda r: (r[1], r[0]))
# Releases are found by date, so two on one day would need a decision: stop and look.
same = sorted({r[1] for r in releases if sum(x[1] == r[1] for x in releases) > 1})
assert not same, f"two releases on one day: {same}"
years = sorted({r[1][:4] for r in releases})

ADD = '"add"?: bool (keep the releases already on the map, instead of replacing the top one)'
STEP = [
    ("latest", "Latest", "The newest release of Esri's World Imagery.", "raster_layer"),
    ("older", "Older", "The release before the one on the map (the latest when there is none).", "prev_extent"),
    ("newer", "Newer", "The release after the one on the map (the latest when there is none).", "next_extent"),
    ("oldest", "Oldest", "The first release, from February 2014.", "history"),
]
commands = [{"id": i, "label": label, "help": help, "icon": icon, "params": "{" + ADD + "}"} for i, label, help, icon in STEP]
for y in years:
    commands.append({"id": f"year-{y}", "label": y, "help": "The year's last release.", "icon": "history", "params": "{" + ADD + "}"})
commands += [
    {"id": "release", "label": "Go to Release", "help": "The release on or before a date, or by its number.", "icon": "history",
     "params": '{"date"?: "2019-05-08" | "2019-05" | "2019" (the last release on or before it), "release"?: number (a release number, as in "releases"), ' + ADD + "}"},
    {"id": "info", "label": "Imagery\nInfo", "help": "Where the imagery at the map's centre comes from: its capture date, source and resolution, with its footprint as a layer.",
     "icon": "identify", "params": '{"lon"?: number, "lat"?: number (default: the map\'s centre)}'},
    {"id": "releases", "label": "Releases", "help": "How many releases there are, and which is on the map.", "icon": "stats", "params": "{}"},
    {"id": "clear", "label": "Remove", "help": "Remove the Wayback imagery, and the imagery source layers, from the map.", "icon": "delete", "params": "{}"},
]
for c in commands:
    if c["params"] == "{}":
        del c["params"]

tab = {"label": "Wayback", "groups": [
    {"label": "Release", "items": ["latest", "older", "newer", "oldest"]},
    {"label": "Year", "items": [{"menu": "Year", "icon": "history", "help": "The last release of a year.", "items": [f"year-{y}" for y in reversed(years)]}]},
    {"label": "Inspect", "items": ["info", "releases"]},
    {"label": "Manage", "items": ["clear"]},
]}
assert len(commands) <= 64 and len(years) <= 32

path = root / "plugin.json"
old = json.loads(path.read_text()) if path.exists() else {}
manifest = {
    "id": "historical-imagery", "name": "Historical Imagery", "version": old.get("version", "0.1.0"),
    "description": "Esri's World Imagery Wayback: every archived release of World Imagery since 2014, as a basemap you can step through by release or year, with the capture date, source and resolution of the imagery at any place.",
    "author": "opengeos", "homepage": "https://github.com/opengeos/rustgis-plugins/tree/main/plugins/historical-imagery",
    "license": "MIT", "entry": "plugin.wasm", "abi": 1, "minRustgisVersion": "0.3.0",
    "permissions": ["edit", "network"],
    "commands": commands, "tab": tab, "i18n": old.get("i18n", {}),
}
path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

lines = [
    "//! The World Imagery Wayback releases baked into the plugin, oldest first: (release number, date,",
    "//! metadata service). Newer ones are read from Esri when needed.",
    f"//! Generated by `generate.py` from Esri's list ({len(releases)} releases, {releases[0][1]} to {releases[-1][1]}).",
    "",
    "#[rustfmt::skip]",
    "pub const RELEASES: &[(u32, &str, &str)] = &[",
]
for num, date, meta in releases:
    lines.append(f'    ({num}, "{date}", "{meta}"),')
lines.append("];")
(root / "src" / "releases.rs").write_text("\n".join(lines) + "\n")
print(len(releases), "releases,", len(commands), "commands")
