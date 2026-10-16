# Development

The app is plain Python. The GUI uses PyQt6, the plotting and preview use matplotlib, rasters are read
with rasterio, geometry uses shapely and scipy, and G-code is written by vpype. All application code is
in the `MoonPlotter/` folder. Run everything from that folder, because data paths in `config.py` are
relative.

## Module map

| Module | Responsibility |
|---|---|
| `main.py` | The `PlotterApp` window: sidebar, source map, preview, layers, editing tools, projects, export. Also holds the background thread classes for missions and image tracing. |
| `config.py` | Paper sizes and colours, built-in pens, `DATA_FILES`, plotter and G-code constants, pen persistence (`plotter_pens.json`) |
| `workers.py` | `RenderWorker` (`QThread`): reads the DEM or colour window for a selection and runs the contour and fill engines |
| `contour_engine.py` | Contours, index lines, Tanaka shading, below-sea-level split, borders, auto interval |
| `hatch_engine.py` | Fills: crosshatch, scanlines, squiggles, stipple, hachures. Colour separation (CIELAB ink-mix search) and the ink compositing used by the preview. |
| `decorations.py` | Title block, scale bar, north arrow, graticule, frame, and the text-blanking masks |
| `stroke_font.py` | Hershey single-stroke lettering (fonts are read from the installed vpype package) |
| `sites.py` | Points of interest for each body |
| `regions.py` | Regional rasters: registry, remote fetch (windowed GeoTIFF, Copernicus tiles, XYZ tiles) and GeoTIFF import |
| `net.py` | HTTP download with fallbacks for HTTPS-inspecting networks |
| `missions.py` | Mission catalogue, JPL Horizons download and cache, planet orbits from Keplerian elements |
| `mission_plot.py` | Lays out a mission chart in paper millimetres: compressed scale, orbits, paths, label placement |
| `image_trace.py` | Image loading, k-means colour reduction, outline tracing, hatch fill, cut line, edge detection |
| `calibration.py` | The pen calibration sheet |
| `exporter.py` | G-code header and footer, pen lifts, pen changes, vpype command, time estimate, bed-limit checks. It has no Qt code. |
| `project_io.py` | Project files (`.topo.json`) and style presets (`presets.json`) |
| `pen_dialog.py`, `export_dialog.py`, `data_dialog.py` | Dialogs |
| `ui_widgets.py`, `theme.py` | Reusable widgets (collapsible sections, layer panel, swatch combo box, wheel guard) and the Qt style sheet |
| `tools/` | One-off scripts, such as building the Lake Tahoe bathymetry file |

`main_v1.py` and `main_matplotlib.py` are older versions kept for reference.

## How a plot is represented

Everything drawable is a list of **segments**. Each segment is a dict:

```python
{'type': 'dem' (contour) | 'hatch' | 'stipple' | 'border' (everything else),
 'layer': 'contours' | 'fill' | 'borders' | 'sites' | 'decor' | 'orbits' | 'img:1' | ...,
 'seg': np.ndarray of shape (n, 2),      # polyline in data coordinates
 'level': elevation_ft_or_0,
 'pen_id': 'base'}                        # key into the pens dict
```

The coordinate frame depends on the mode:

- **Map modes** (Earth, Moon, Mars) use degrees of longitude and latitude. The view extent is the
  selection.
- **Paper modes** (calibration sheet, Solar System, Image) use millimetres. The frame is
  `[0, paper_w, -paper_h/2, paper_h/2]`.

Hand edits (`custom_items`: lines and text) are stored separately. The undo stack snapshots the segment
pens and custom items.

Two layer groups get special treatment in `main.py`:

- `TOP_LAYERS` are drawn above the text-blanking boxes and are excluded from paint picking.
- `FINE_LAYERS`, and any layer starting with `img:`, are exported without vpype's simplify, merge and
  2 mm length filter, so lettering and small marks survive.

## Data flow

**Map render**: **Draw Map** → `PlotterApp._start_render()` → `RenderWorker.run()`:

1. read the DEM window (regional file if one covers the selection),
2. contours,
3. optionally read the colour window, then fill,
4. return segments to `on_render_success()`.

Map furniture is then added by `decorations.build_decorations()`. The preview is drawn by
`render_canvas()`, or by `_refresh_ink_sim()` when a fill is visible.

**Export**: `export_action()` → `ExportDialog` → for each pen:

1. `_lines_for_pen()`, which applies text blanking,
2. a temporary SVG at true paper size,
3. `vpype_cli.execute(exporter.vpype_command(...))`,
4. `exporter.wrap_vpype_body()` (adds lifts and feeds),
5. `header` / `pen_change` / `footer`, then a bed-limit check.

## Tests

```bash
cd MoonPlotter
python tests/test_engines.py          # or: python -m pytest tests
```

The tests cover:

- the contour, hatch and colour-separation engines,
- G-code structure and Z safety (homing, safe heights, lifts),
- decorations and lettering,
- mission parsing, planet positions and layout,
- image tracing,
- project round-trips.

Mission tests use small saved Horizons responses in `tests/fixtures/`, so they run offline. The GUI
itself is not covered by the tests. Check UI changes by running the app.

## Common changes

- **A different plotter**: see [Plotter setup](plotter-setup.md#other-machines). Most of it is in
  `config.py` and `exporter.py`.
- **A new mission**: add an entry to `MISSIONS` in `missions.py` with these fields:
  - `key`, `name`,
  - `hid` (the Horizons spacecraft ID, usually negative, for example Voyager 2 = `-32`),
  - `launch` and optional `end` dates,
  - `events` as `(date, body, label)` flybys,
  - `escapes`, `apl` and `apl_note`.

  Horizons only serves dates inside a spacecraft's ephemeris span. `fetch_mission()` retries inside the
  span automatically when Horizons reports one.
- **A new regional data source**: add an entry to `REMOTE_SOURCES` in `regions.py`. Windowed global
  GeoTIFFs only need a URL, extent and units. Tiled services use `'tiles': 'xyz'`.
- **A new point of interest**: add a tuple to `SITES` in `sites.py`.
- **A new built-in pen**: add it to `default_pens()` in `config.py`. Users' own pens live in
  `plotter_pens.json`.

## Contributing

Issues and pull requests are welcome. Please:

- keep engine modules free of Qt, so they stay testable,
- add or extend a test in `tests/test_engines.py` for engine changes,
- run the tests before opening a pull request,
- don't commit large data files (`*.tif` is in `.gitignore`) or `__pycache__`.
