# Map Explorer

A tab of its own for exploring the map: jump to a continent or a random place, select the largest features or a random sample, and summarize a layer's numeric fields.

## Its tab

**Explorer**, at the end of the ribbon's tab strip:

- **Go To**: a **Continent ▾** menu (6 entries), Random Place
- **Select**: Largest, Random Sample
- **Inspect**: Layer Report, Field Summary (tool)

See the `tab` in [plugin.json](plugin.json) for how it's declared.

## Commands

- **Africa** (`plugin:map-explorer/go-africa`): Go to Africa.
- **Asia** (`plugin:map-explorer/go-asia`): Go to Asia.
- **Europe** (`plugin:map-explorer/go-europe`): Go to Europe.
- **North America** (`plugin:map-explorer/go-north-america`): Go to North America.
- **South America** (`plugin:map-explorer/go-south-america`): Go to South America.
- **Oceania** (`plugin:map-explorer/go-oceania`): Go to Oceania.
- **Random Place** (`plugin:map-explorer/random-place`): Zoom to a random feature of the selected layer.
- **Largest** (`plugin:map-explorer/select-largest`): Select the 10 largest features of the selected layer (by area, or length for lines) and zoom to them.
- **Random Sample** (`plugin:map-explorer/select-sample`): Select a random tenth of the selected layer's features.
- **Layer Report** (`plugin:map-explorer/layer-report`): Show the selected layer's feature count, fields and selection in the status bar.

## Tools

- **Field Summary** (`plugin:map-explorer/field-summary`): A table of count, minimum, maximum, mean and sum for each numeric field of a layer.

## Permissions

edit (selecting features)

## Build

```sh
cargo build --release --target wasm32-unknown-unknown -p map-explorer
```
